"""Focused contract tests for repository test-runner admission."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "run-plugin-tests.py"
_previous_path = sys.path.copy()
sys.path.insert(0, str(SCRIPT.parent))
try:
    _spec = importlib.util.spec_from_file_location("run_plugin_tests", SCRIPT)
    assert _spec and _spec.loader
    runner = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = runner
    _spec.loader.exec_module(runner)

    import plugin_test_containment as containment
    import pytest_portfolio_guard as portfolio_guard
    import _admission_protocol as admission
finally:
    sys.path[:] = _previous_path


def test_unreaped_tree_retains_sandbox_without_cleanup(monkeypatch, tmp_path, capsys):
    plugin = tmp_path / "alpha"
    sandbox_paths = []

    def run_contained(*args, **kwargs):
        sandbox_paths.append(kwargs["sandbox"])
        (kwargs["sandbox"] / "still-owned.txt").write_text("preserve")
        raise runner.UnreapedProcessError("forced teardown failure")

    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(runner, "_has_suite", lambda name: True)
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: plugin)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path("fake-python"))
    monkeypatch.setattr(runner, "_test_file_groups",
                        lambda *a, **k: [[plugin / "tests" / "test_one.py"]])
    monkeypatch.setattr(runner, "run_contained", run_contained)
    with pytest.raises(runner.UnreapedProcessError, match="forced teardown failure"):
        runner.run_plugin(
            "alpha", "uv", reinstall=False, kexpr=None, limits=runner.Limits(),
            plugin_timeout=60, test_timeout=30, max_files_per_subsuite=25,
        )
    sandbox = sandbox_paths[0]
    try:
        assert (sandbox / "still-owned.txt").read_text() == "preserve"
        assert (sandbox / "t").is_dir()
        assert (sandbox / "t" / "g1").is_dir()
        if os.name != "nt":
            assert (sandbox / "t").stat().st_mode & 0o077 == 0
        assert "[KEEP] Unreaped process tree: sandbox retained at" in capsys.readouterr().err
    finally:
        shutil.rmtree(sandbox)


@pytest.fixture
def cleanup_runner(monkeypatch, tmp_path):
    plugin = tmp_path / "alpha"
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(runner, "_has_suite", lambda name: True)
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: plugin)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path("fake-python"))
    monkeypatch.setattr(runner, "_test_file_groups", lambda *a, **k: [
        [plugin / "tests" / "test_one.py"], [plugin / "tests" / "test_two.py"],
    ])
    monkeypatch.setattr(runner, "run_contained", lambda *a, **k: 0)

    def run():
        return runner.run_plugin(
            "alpha", "uv", reinstall=False, kexpr=None, limits=runner.Limits(),
            plugin_timeout=60, test_timeout=30, max_files_per_subsuite=25,
        )

    return run


def test_plugin_budget_includes_final_sandbox_cleanup(
    monkeypatch, capsys, cleanup_runner,
):
    clock = [0.0]
    original_directory = runner._TestSandbox

    class Directory(original_directory):
        def __exit__(self, *args):
            result = super().__exit__(*args)
            clock[0] = 61.0
            return result

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner, "_TestSandbox", Directory)
    assert cleanup_runner() == 124
    assert "including sandbox cleanup: 61.00s elapsed" in capsys.readouterr().err


def test_budget_exhausted_between_groups_still_reports_final_cleanup(
    monkeypatch, capsys, cleanup_runner,
):
    clock = [0.0]
    calls = []
    original_directory = runner._TestSandbox

    class Directory(original_directory):
        def __exit__(self, *args):
            result = super().__exit__(*args)
            clock[0] = 65.0
            return result

    def run(*args, **kwargs):
        calls.append(args)
        clock[0] = 61.0
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner, "_TestSandbox", Directory)
    assert cleanup_runner() == 124
    assert len(calls) == 1
    assert "including sandbox cleanup: 65.00s elapsed" in capsys.readouterr().err


def test_preparation_uses_the_same_aggregate_budget(
    monkeypatch, capsys, cleanup_runner,
):
    clock = [0.0]
    calls = []

    def prepare(*args, **kwargs):
        clock[0] = 61.0
        return Path("fake-python")

    monkeypatch.setattr(runner, "_ensure_venv", prepare)
    monkeypatch.setattr(runner, "run_contained", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    assert cleanup_runner() == 124
    assert calls == []
    assert "(preparation included)" in capsys.readouterr().err


def test_reaped_group_cleanup_overlaps_the_next_group(
    monkeypatch, tmp_path, capsys, cleanup_runner,
):
    started = threading.Event()
    release = threading.Event()
    removed = []
    workers = set()
    original_remove = runner.tempfile.TemporaryDirectory._rmtree
    calls = []

    def remove(name, *args, **kwargs):
        if Path(name).name in {"g1", "g2"}:
            workers.add(threading.get_ident())
        if Path(name).name == "g1":
            started.set()
            assert release.wait(5), "next group never released cleanup"
        original_remove(name, *args, **kwargs)
        removed.append(Path(name))

    def run_contained(command, **kwargs):
        calls.append(command)
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        assert group.is_dir()
        (group / "owned.txt").write_text("disposable")
        cache = kwargs["sandbox"] / "s" / "example" / "data"
        if len(calls) == 1:
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"immutable shared seed")
            assert containment._tree_size(kwargs["sandbox"]) >= cache.stat().st_size
        else:
            try:
                assert started.wait(5), "cleanup did not overlap the next group"
                assert cache.read_bytes() == b"immutable shared seed"
            finally:
                release.set()
        return 0

    monkeypatch.setattr(runner, "run_contained", run_contained)
    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    try:
        assert cleanup_runner() == 0
    finally:
        release.set()
    assert len(calls) == 2
    assert [path.name for path in removed[:2]] == ["g1", "g2"]
    assert len(workers) == 1
    assert not list(tmp_path.glob("ce-alph-*"))
    assert "including preparation and cleanup" in capsys.readouterr().out


@pytest.mark.parametrize("interruption", [None, KeyboardInterrupt, runner.UnreapedProcessError])
def test_cleanup_failure_is_explicit_and_cannot_mask_unreaped_tree(
    monkeypatch, tmp_path, capsys, cleanup_runner, interruption,
):
    original_remove = runner.tempfile.TemporaryDirectory._rmtree
    roots = []
    groups = []

    def remove(name, *args, **kwargs):
        if Path(name).name == "g1":
            raise OSError("group removal refused")
        return original_remove(name, *args, **kwargs)

    def run(command, **kwargs):
        roots.append(kwargs["sandbox"])
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        groups.append(group)
        (group / "owned.txt").write_text("preserve")
        if len(groups) == 2 and interruption is not None:
            raise interruption("second group interrupted")
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    try:
        with pytest.raises(interruption or OSError):
            cleanup_runner()
        assert "Group sandbox cleanup failed: group removal refused" in capsys.readouterr().err
        if interruption is runner.UnreapedProcessError:
            assert (groups[1] / "owned.txt").read_text() == "preserve"
        else:
            assert not roots[0].exists()
    finally:
        if roots and roots[0].exists():
            shutil.rmtree(roots[0])


@pytest.mark.parametrize("returncode", [1, 5])
def test_failed_group_is_cleaned_without_starting_another(
    monkeypatch, cleanup_runner, returncode,
):
    roots = []

    def run(*args, **kwargs):
        roots.append(kwargs["sandbox"])
        return returncode

    monkeypatch.setattr(runner, "run_contained", run)
    assert cleanup_runner() == returncode
    assert len(roots) == 1
    assert not roots[0].exists()


@pytest.mark.parametrize("seam", ["wait", "shutdown", "both"])
@pytest.mark.parametrize("unreaped", [False, True])
def test_cleanup_wait_interruptions_join_before_preserving_ownership_error(
    monkeypatch, capsys, cleanup_runner, seam, unreaped,
):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    roots = []
    calls = []
    interruptions = []
    original_remove = runner.tempfile.TemporaryDirectory._rmtree

    def remove(name, *args, **kwargs):
        if Path(name).name == "g1":
            started.set()
            assert release.wait(5), "cleanup was not released after interruption"
        original_remove(name, *args, **kwargs)
        if Path(name).name == "g1":
            finished.set()

    class Pool(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            result = future.result

            def interrupt_once(*args, **kwargs):
                if seam in {"wait", "both"} and "wait" not in interruptions:
                    assert not future.done()
                    interruptions.append("wait")
                    raise KeyboardInterrupt("cleanup wait interrupted")
                release.set()
                return result(*args, **kwargs)

            monkeypatch.setattr(future, "result", interrupt_once)
            return future

        def shutdown(self, *args, **kwargs):
            if seam in {"shutdown", "both"} and "shutdown" not in interruptions:
                assert finished.is_set()
                interruptions.append("shutdown")
                raise KeyboardInterrupt("executor shutdown interrupted")
            return super().shutdown(*args, **kwargs)

    def run(command, **kwargs):
        roots.append(kwargs["sandbox"])
        calls.append(command)
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        (group / "owned.txt").write_text("preserve")
        if len(calls) == 2:
            assert started.wait(5)
            if unreaped:
                raise runner.UnreapedProcessError("second group still owns its files")
        return 0

    monkeypatch.setattr(runner, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(runner, "run_contained", run)
    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    try:
        expected = runner.UnreapedProcessError if unreaped else KeyboardInterrupt
        with pytest.raises(expected):
            cleanup_runner()
        assert finished.is_set()
        assert interruptions == (["wait", "shutdown"] if seam == "both" else [seam])
        if unreaped:
            assert (roots[0] / "t" / "g2" / "owned.txt").read_text() == "preserve"
        else:
            assert not roots[0].exists()
        assert "[PASS]" not in capsys.readouterr().out
    finally:
        release.set()
        if roots and roots[0].exists():
            shutil.rmtree(roots[0])


@pytest.mark.parametrize("cancellation", [None, KeyboardInterrupt, SystemExit])
def test_final_sandbox_cleanup_failure_cannot_mask_cancellation_or_report_pass(
    monkeypatch, tmp_path, capsys, cleanup_runner, cancellation,
):
    original_remove = runner.tempfile.TemporaryDirectory._rmtree

    def remove(name, *args, **kwargs):
        if Path(name).name.startswith("ce-alph-"):
            assert not kwargs.get("ignore_errors", False)
            raise OSError("final sandbox removal refused")
        return original_remove(name, *args, **kwargs)

    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    if cancellation is not None:
        def cancel(*args, **kwargs):
            raise cancellation("stop runner")

        monkeypatch.setattr(runner, "run_contained", cancel)
    try:
        with pytest.raises(cancellation or OSError):
            cleanup_runner()
        output = capsys.readouterr()
        assert "[PASS]" not in output.out
        if cancellation is not None:
            assert "Sandbox cleanup failed: final sandbox removal refused" in output.err
    finally:
        for root in tmp_path.glob("ce-alph-*"):
            shutil.rmtree(root)


@pytest.mark.parametrize("seam", ["wait", "shutdown"])
@pytest.mark.parametrize("pending", [None, OSError, runner.UnreapedProcessError])
@pytest.mark.parametrize("cancellation", [KeyboardInterrupt, SystemExit])
def test_cleanup_cancellation_precedes_errors_but_not_unreaped_ownership(
    monkeypatch, tmp_path, cleanup_runner, seam, pending, cancellation,
):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    roots = []
    calls = []
    interrupted = []
    original_remove = runner.tempfile.TemporaryDirectory._rmtree
    plugin = tmp_path / "alpha"
    monkeypatch.setattr(runner, "_test_file_groups", lambda *a, **k: [
        [plugin / "tests" / f"test_{index}.py"] for index in range(3)
    ])

    def remove(name, *args, **kwargs):
        if Path(name).name == "g1":
            raise OSError("first removal refused")
        if Path(name).name == "g2":
            started.set()
            assert release.wait(5)
        original_remove(name, *args, **kwargs)
        if Path(name).name == "g2":
            finished.set()

    class Pool(ThreadPoolExecutor):
        def submit(self, fn, name):
            future = super().submit(fn, name)
            if Path(name).name == "g2":
                result = future.result

                def interrupt_once(*args, **kwargs):
                    if seam == "wait" and not interrupted:
                        assert not future.done()
                        interrupted.append(seam)
                        raise cancellation("cancel during wait")
                    release.set()
                    return result(*args, **kwargs)

                monkeypatch.setattr(future, "result", interrupt_once)
            return future

        def shutdown(self, *args, **kwargs):
            if seam == "shutdown" and not interrupted:
                assert finished.is_set()
                interrupted.append(seam)
                raise cancellation("cancel during shutdown")
            return super().shutdown(*args, **kwargs)

    def run(command, **kwargs):
        roots.append(kwargs["sandbox"])
        calls.append(command)
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        (group / "owned.txt").write_text("preserve")
        if len(calls) == 3:
            assert started.wait(5)
            if pending is not None:
                raise pending("third group failed")
        return 0

    monkeypatch.setattr(runner, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(runner, "run_contained", run)
    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    try:
        expected = pending if pending is runner.UnreapedProcessError else cancellation
        with pytest.raises(expected):
            cleanup_runner()
        assert interrupted == [seam]
        assert finished.is_set()
        if pending is runner.UnreapedProcessError:
            assert (roots[0] / "t" / "g3" / "owned.txt").read_text() == "preserve"
        else:
            assert not roots[0].exists()
    finally:
        release.set()
        if roots and roots[0].exists():
            shutil.rmtree(roots[0])


def test_real_group_processes_release_readonly_files_before_cleanup(
    monkeypatch, tmp_path, cleanup_runner,
):
    plugin = tmp_path / "alpha"
    tests = plugin / "tests"
    tests.mkdir(parents=True)
    body = (
        "import stat\n"
        "def test_owned_file(tmp_path):\n"
        "    owned = tmp_path / 'readonly.txt'\n"
        "    owned.write_text('owned')\n"
        "    owned.chmod(stat.S_IREAD)\n"
    )
    for name in ("test_one.py", "test_two.py"):
        (tests / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path(sys.executable))
    roots = []
    groups = []

    def run(command, **kwargs):
        roots.append(kwargs["sandbox"])
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        groups.append(group)
        rc = containment.run_contained(command, **kwargs)
        assert list(group.rglob("readonly.txt"))
        return rc

    monkeypatch.setattr(runner, "run_contained", run)
    assert cleanup_runner() == 0
    assert len(groups) == 2 and groups[0] != groups[1]
    assert not roots[0].exists()


@pytest.mark.parametrize("reinstall", [False, True])
@pytest.mark.parametrize("cached", [False, True])
def test_explicit_reinstall_invalidates_uv_cached_path_dependency_wheels(
    tmp_path, monkeypatch, reinstall, cached,
):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    monkeypatch.setattr(runner, "_has_pyproject", lambda _name: True)
    monkeypatch.setattr(runner, "_has_dev_extra", lambda _name: True)
    monkeypatch.setattr(runner, "_dep_fingerprint", lambda _name: "current")
    root = runner.VENV_ROOT / "example"
    root.mkdir(parents=True)
    python = root / "fixture-python"
    monkeypatch.setattr(runner, "_venv_python", lambda _name: python)
    if cached:
        python.touch()
        (root / ".dep-fingerprint").write_text("current", encoding="utf-8")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[:2] == ["uv", "venv"]:
            python.touch()
        return subprocess.CompletedProcess(command, 0)

    def install(uv, arguments, **kwargs):
        assert kwargs == {
            "cwd": plugin, "trusted_source_roots": (runner.REPO,), "check": True,
            "retained_resource_roots": (),
        }
        return run([uv, "pip", "install", *arguments])

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "run_uv_install", install)
    assert runner._ensure_venv("example", "uv", reinstall=reinstall) == python
    installs = [command for command in calls if command[:3] == ["uv", "pip", "install"]]
    assert len(installs) == int(reinstall or not cached)
    if installs:
        assert installs[0].count("--reinstall") == int(reinstall)
        assert installs[0][3:5] == ["--python", str(python)]
        assert installs[0][5:7] == ["-e", ".[dev]"]


@pytest.fixture
def selectable_tests(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    tests = tmp_path / "tests"
    (tests / "nested").mkdir(parents=True)
    for name in ("test_selected.py", "test_unrelated.py", "nested/test_child.py"):
        (tests / name).write_text("def test_example(): pass\n", encoding="utf-8")
    return tests


@pytest.mark.parametrize("name,extra", [
    ("agent-index", "auth"), ("agent-pull-requests", "service-gateway"),
    ("agent-dispatch", "service-gateway"), ("agent-ssh", ""),
])
def test_preparation_selects_same_checkout_fleet_dependency(tmp_path, monkeypatch, name, extra):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    plugin = tmp_path / "plugins" / name
    plugin.mkdir(parents=True)
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    (plugin / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\n[project.optional-dependencies]\ndev = ["fleet-manager'
        + (f"[{extra}]" if extra else "") + '>=0.1.0.dev1"]\n',
        encoding="utf-8",
    )
    fleet = tmp_path / "fleet-manager"
    fleet.mkdir()
    metadata = fleet / "pyproject.toml"
    metadata.write_text('[project]\nname = "fleet-manager"\nversion = "0.1.0.dev1"\n', encoding="utf-8")
    for resource in runner.standalone_build_resources("fleet-manager", source_root=tmp_path):
        resource.mkdir(parents=True)
    if name == "agent-dispatch":
        replay = tmp_path / "plugins" / "agent-pull-requests"
        replay.mkdir()
        (replay / "pyproject.toml").write_text(
            '[project]\nname = "agent-pull-requests"\n', encoding="utf-8",
        )
    (runner.VENV_ROOT / name).mkdir(parents=True)
    before = runner._dep_fingerprint(name)
    metadata.write_text('[project]\nname = "fleet-manager"\nversion = "0.1.0.dev2"\n', encoding="utf-8")
    assert runner._dep_fingerprint(name) != before
    calls = []
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )
    retained = []

    def prepare(uv, arguments, **kwargs):
        retained.append(kwargs["retained_resource_roots"])
        return runner.subprocess.run([uv, "pip", "install", *arguments])

    monkeypatch.setattr(runner, "run_uv_install", prepare)
    runner._ensure_venv(name, "fixture-uv", reinstall=False)
    install = calls[-1]
    if name == "agent-dispatch":
        assert str(tmp_path / "plugins" / "agent-pull-requests") + "[fleet]" in install
    assert install[-len(runner.RUNNER_DEPENDENCIES) - 2:] == [
        "-e", str(fleet) + (f"[{extra}]" if extra else ""), *runner.RUNNER_DEPENDENCIES,
    ]
    assert not any("http" in argument for argument in install)
    assert retained == [(
        tmp_path / "libs" / "versioned-runtime",
        tmp_path / "libs" / "installer-engine",
        tmp_path / "libs" / "agent-procutil" / "src" / "agent_procutil",
    )]


def test_dispatch_preparation_fingerprints_same_checkout_replay_dependency(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: tmp_path / "plugins" / name)
    metadata = tmp_path / "plugins" / "agent-pull-requests" / "pyproject.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('[project]\nname = "agent-pull-requests"\nversion = "1.0"\n', encoding="utf-8")
    fleet = tmp_path / "fleet-manager"
    fleet.mkdir()
    (fleet / "pyproject.toml").write_text('[project]\nname = "fleet-manager"\n', encoding="utf-8")
    for resource in runner.standalone_build_resources("fleet-manager", source_root=tmp_path):
        resource.mkdir(parents=True)
    before = runner._dep_fingerprint("agent-dispatch")
    metadata.write_text('[project]\nname = "agent-pull-requests"\nversion = "1.1"\n', encoding="utf-8")
    assert runner._dep_fingerprint("agent-dispatch") != before


def test_ssh_optional_sdk_is_not_misattributed_as_a_canonical_library():
    import tomllib

    metadata = tomllib.loads(
        (runner.REPO / "plugins" / "agent-ssh" / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert "fleet-manager" not in metadata["tool"]["uv"]["sources"]
    assert any(item.startswith("fleet-manager>=") for item in metadata["project"]["optional-dependencies"]["fleet"])
    assert any(item.startswith("fleet-manager>=") for item in metadata["project"]["optional-dependencies"]["dev"])


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows editable staging contract")
def test_windows_editable_metadata_is_retained_near_all_sources(tmp_path, monkeypatch):
    import tomllib

    policy = sys.modules[runner.run_uv_install.__module__]
    checkout = tmp_path / "checkout"
    projects = [checkout / "plugins" / "fixture", checkout / "fleet-manager"]
    for index, source in enumerate(projects):
        (source / "src").mkdir(parents=True)
        (source / "pyproject.toml").write_text(
            '[build-system]\nrequires=[]\nbuild-backend="setuptools.build_meta"\n'
            f'[project]\nname="fixture-{index}"\nversion="1.0"\ndependencies=[]\n'
            '[tool.setuptools.packages.find]\nwhere=["src"]\n', encoding="utf-8",
        )
    retained = []

    def run(command, **kwargs):
        if "--show-settings" in command:
            return subprocess.CompletedProcess(command, 0, """PipInstallSettings { settings: PipSettings {
                index_locations: IndexLocations { indexes: [], flat_index: [], },
                exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
            }, }""", "")
        root = Path(kwargs["cwd"])
        retained.append(root)
        assert root.is_relative_to(checkout.parent)
        assert all(not root.is_relative_to(source) for source in projects)
        manifests = list(root.parents[1].rglob("pyproject.toml"))
        assert len(manifests) == len(projects)
        for manifest in manifests:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
            relative = data["tool"]["setuptools"]["package-dir"][""]
            target = (manifest.parent / relative).resolve()
            assert target in [source / "src" for source in projects]
            # Use the real metadata rename shape, with an absolute origin and
            # a lexical relative destination, rather than resolving both away.
            metadata = target / "fixture.egg-info"
            metadata.mkdir()
            temporary = metadata / "temporary"
            temporary.write_text("fixture", encoding="utf-8")
            destination = manifest.parent / relative / "fixture.egg-info" / "PKG-INFO"
            assert len(str(destination)) < 260
            temporary.replace(destination)
            assert (metadata / "PKG-INFO").read_text(encoding="utf-8") == "fixture"
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(policy.subprocess, "run", run)
    result = policy.run_uv_install(
        "fixture-uv", ["--no-config", "-e", str(projects[0]), "-e", str(projects[1])],
        cwd=projects[0], trusted_source_roots=(checkout,),
    )
    assert result.returncode == 0 and retained and not retained[0].exists()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows retained-file copy")
def test_windows_retained_file_copy_preserves_long_path_bytes(tmp_path):
    policy = sys.modules[runner.run_uv_install.__module__]
    root = Path("\\\\?\\" + str(tmp_path.resolve())) / ("nested-" * 15) / ("snapshot-" * 8)
    root.mkdir(parents=True)
    source = root / "installer_dependency_policy.py"
    source.write_bytes(b"fixture retained source\n")
    destination = root / "copied_installer_dependency_policy.py"
    plain_source = Path(str(source).removeprefix("\\\\?\\"))
    plain_destination = Path(str(destination).removeprefix("\\\\?\\"))
    assert len(str(plain_destination)) > 260
    policy._copy_retained_file(plain_source, plain_destination)
    assert destination.read_bytes() == source.read_bytes()
    with policy._temporary_inputs(tmp_path) as temporary:
        owned = Path(policy._native_retained_path(temporary))
        nested = owned / ("snapshot-" * 14) / ("dependency-" * 5)
        nested.mkdir(parents=True)
        (nested / "installer_dependency_policy.py").write_bytes(b"retained fixture\n")
    assert not owned.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows configuration path normalization")
@pytest.mark.parametrize("selection", ["argument", "environment"])
def test_windows_parent_relative_configuration_keeps_native_resolver_policy(tmp_path, selection):
    policy = sys.modules[runner.run_uv_install.__module__]
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\n'
        '[project]\nname="owned-fixture"\nversion="1.0"\ndependencies=[]\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.toml").write_text('[pip]\nexclude-newer = "9 days"\n', encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
    environment["UV_OFFLINE"] = "1"
    arguments = [str(source)]
    if selection == "argument":
        arguments = ["--config-file", "../uv.toml", *arguments]
    else:
        environment["UV_CONFIG_FILE"] = "../uv.toml"
    uv = shutil.which("uv")
    assert uv, "native UV is required for configuration provenance coverage"
    admitted = policy.prepare_uv_install(
        uv, arguments, cwd=source, env=environment, trusted_source_roots=(source,),
    )
    assert "--exclude-newer" in admitted
    assert ".." not in Path(policy._native_retained_path(source / ".." / "uv.toml")).parts
    assert policy._toml(source / ".." / "uv.toml")["pip"]["exclude-newer"] == "9 days"


def test_explicit_test_files_preserve_default_grouping_and_filter_only_collection(selectable_tests):
    tests = selectable_tests
    assert len(runner._test_file_groups("example", 1)) == 3
    assert len(runner._test_file_groups("example", 1, filtered=True)[0]) == 3
    assert runner._test_file_groups(
        "example", 1, filtered=True,
        test_files=("tests/test_selected.py", "tests/nested/test_child.py", "tests/test_selected.py"),
    ) == [[tests / "nested/test_child.py", tests / "test_selected.py"]]


def test_manager_default_discovery_excludes_only_its_prepared_native_lane():
    root = runner._plugin_dir("worktree-manager")
    native = set((root / "tests" / "native").glob("test_*.py"))
    assert {path.name for path in native} == {
        "test_manager_runtime_install.py", "test_manager_mux_callbacks.py",
        "test_manager_updater_recovery.py",
        "test_launch_attempts_native.py", "test_mux_discovery_native.py",
        "test_psmux_home_directory.py", "test_warm_picker_lifetime.py",
    }
    default = {path for group in runner._test_file_groups("worktree-manager", 25) for path in group}
    assert default == set((root / "tests").rglob("test_*.py")) - native


def test_manager_explicit_native_selection_remains_available():
    root = runner._plugin_dir("worktree-manager")
    files = tuple(sorted(
        path.relative_to(root).as_posix() for path in (root / "tests" / "native").glob("test_*.py")
    ))
    assert runner._test_file_groups("worktree-manager", 25, test_files=files) == [
        [root / value for value in files],
    ]


@pytest.mark.parametrize("setting", [
    '["native", "build_*", "test_default.py"]', '"native build_* test_default.py"',
])
@pytest.mark.parametrize("filtered", [False, True])
def test_default_discovery_honors_pytest_directory_patterns_but_not_file_names(
    tmp_path, monkeypatch, setting, filtered,
):
    root = tmp_path / "fixture"
    for name in ("test_default.py", "native/test_native.py", "nested/native/test_nested.py",
                 "build_fixture/test_build.py", "nested/test_kept.py"):
        path = root / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\nnorecursedirs = " + setting + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: root)
    default = {path.relative_to(root).as_posix()
               for group in runner._test_file_groups("fixture", 25, filtered=filtered) for path in group}
    assert default == {"tests/test_default.py", "tests/nested/test_kept.py"}
    assert runner._test_file_groups(
        "fixture", 25, test_files=("tests/native/test_native.py",),
    ) == [[root / "tests" / "native" / "test_native.py"]]


@pytest.mark.parametrize("value", ["./tests/test_selected.py", "tests//test_selected.py"])
def test_interactive_test_file_paths_are_normalized_without_changing_manifest_policy(selectable_tests, value):
    assert runner._test_file_groups("example", 25, test_files=(value,)) == [
        [selectable_tests / "test_selected.py"],
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows native path separators")
def test_interactive_test_file_accepts_native_windows_separators(selectable_tests):
    assert runner._test_file_groups(
        "example", 25, test_files=("tests\\test_selected.py",),
    ) == [[selectable_tests / "test_selected.py"]]


@pytest.mark.parametrize("path", [
    "missing.py", "../test_selected.py", "test_selected.py", "nested",
])
def test_explicit_test_files_reject_unknown_or_nonowned_paths_before_preparation(
    selectable_tests, monkeypatch, path,
):
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **kw: pytest.fail("prepared invalid selection"))
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner.run_plugin(
            "example", "unused", reinstall=False, kexpr=None, test_files=(path,),
            limits=runner.Limits(), plugin_timeout=60, test_timeout=10,
            max_files_per_subsuite=25,
        )


def test_explicit_test_files_reject_absolute_and_linked_escape(selectable_tests, tmp_path):
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner._test_file_groups("example", 25, test_files=(str(selectable_tests / "test_selected.py"),))
    outside = tmp_path / "test_outside.py"
    outside.write_text("raise AssertionError('must not collect')\n", encoding="utf-8")
    try:
        (selectable_tests / "test_link.py").symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks unavailable")
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner._test_file_groups("example", 25, test_files=("tests/test_link.py",))


@pytest.mark.parametrize("arguments", [
    ["example", "other", "--test-file", "tests/test_selected.py"],
    ["example", "--prepare-only", "--test-file", "tests/test_selected.py"],
])
def test_explicit_test_files_require_one_executing_plugin(arguments):
    with pytest.raises(SystemExit) as error:
        runner.main(arguments)
    assert error.value.code == 2


def test_explicit_test_files_reach_contained_pytest_without_unrelated_modules(
    selectable_tests, monkeypatch,
):
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **kw: Path(sys.executable))
    calls = []
    monkeypatch.setattr(runner, "run_contained", lambda command, **kw: calls.append(command) or 0)
    assert runner.run_plugin(
        "example", "unused", reinstall=False, kexpr="example",
        test_files=("tests/test_selected.py",), limits=runner.Limits(),
        plugin_timeout=60, test_timeout=10, max_files_per_subsuite=25,
    ) == 0
    assert len(calls) == 1
    assert str(Path("tests") / "test_selected.py") in calls[0]
    assert not any("unrelated" in argument for argument in calls[0])
    assert calls[0][-3:-1] == ["-k", "example"]
def test_explicit_file_selection_avoids_unrelated_collection(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    for name in ("test_reconcile.py", "test_binding.py", "test_unrelated.py"):
        (tests / name).touch()
    groups = runner._test_file_groups(
        "example", 25, filtered=True,
        test_files=("tests/test_reconcile.py", "tests/test_binding.py", "tests/test_binding.py"),
    )
    assert groups == [[tests / "test_binding.py", tests / "test_reconcile.py"]]
    assert runner._test_file_groups("example", 25) == [[
        tests / "test_binding.py", tests / "test_reconcile.py", tests / "test_unrelated.py",
    ]]


@pytest.mark.parametrize("value", [
    "../test_outside.py", "tests/../test_outside.py", "tests/test_missing.py",
    "tests/helper.py", "tests/test_directory.py",
])
def test_explicit_file_selection_refuses_invalid_paths(monkeypatch, tmp_path, value):
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    (tests / "helper.py").touch()
    (tests / "test_directory.py").mkdir()
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner._test_file_groups("example", 25, test_files=(value,))


def test_explicit_file_selection_refuses_absolute_path(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    target = tests / "test_absolute.py"
    target.touch()
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner._test_file_groups("example", 25, test_files=(str(target),))


@pytest.mark.skipif(os.name == "nt", reason="POSIX unprivileged symlink creation")
def test_explicit_file_selection_refuses_linked_file_or_parent(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    target = tests / "test_target.py"
    target.touch()
    (tests / "test_link.py").symlink_to(target)
    (tests / "linked").symlink_to(tests, target_is_directory=True)
    for value in ("tests/test_link.py", "tests/linked/test_target.py"):
        with pytest.raises(ValueError, match="existing, unlinked"):
            runner._test_file_groups("example", 25, test_files=(value,))


@pytest.mark.parametrize("args", [
    ["--all"], ["--changed"], ["example", "other"], ["example", "--prepare-only"],
])
def test_explicit_file_selection_requires_one_execution_target(args):
    with pytest.raises(SystemExit) as error:
        runner.main([*args, "--test-file", "tests/test_example.py"])
    assert error.value.code == 2


def test_explicit_empty_selection_is_not_a_guard_success(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_empty.py").touch()
    monkeypatch.setattr(runner, "_ensure_venv", lambda *args, **kwargs: Path(sys.executable))
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return 5
    monkeypatch.setattr(runner, "run_contained", run)
    assert runner.run_plugin(
        "example", "uv", reinstall=False, kexpr="missing", guards=True,
        limits=containment.Limits(), plugin_timeout=60, test_timeout=30,
        max_files_per_subsuite=25, test_files=("tests/test_empty.py",),
    ) == 5
    assert len(calls) == 1
    assert calls[0][0][-1] == str(Path("tests") / "test_empty.py")
    assert "--timeout=30" in calls[0][0]
    assert 0 < calls[0][1]["limits"].wall_seconds <= 60


def test_worktrees_native_runtime_files_have_independent_groups():
    assert {
        "test_python_runtime_dev_live.py", "test_python_runtime_install.py",
    } <= runner._INDEPENDENT_SUBSUITE_FILES["agent-worktrees"]


def test_bridge_native_and_performance_files_have_independent_groups(tmp_path, monkeypatch):
    native = {
        "test_auth_runtime.py", "test_live_runtime.py", "test_predecessor_runtime.py",
        "test_relay_adoption.py", "test_runtime_controls.py", "test_runtime_serving.py",
        "test_runtime_transfer.py", "test_startup_imports.py", "test_startup_performance.py",
        "test_worktree_runtime.py",
    }
    assert runner._INDEPENDENT_SUBSUITE_FILES["agent-bridge"] == native
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in native | {"test_a.py", "test_z.py"}:
        (tests / name).touch()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    groups = runner._test_file_groups("agent-bridge", 25)
    assert sorted(path.name for group in groups for path in group) == sorted(
        native | {"test_a.py", "test_z.py"},
    )
    for name in native:
        assert [tests / name] in groups


@pytest.mark.parametrize("filtered", [False, True])
def test_independent_native_groups_preserve_every_collected_file(tmp_path, monkeypatch, filtered):
    tests = tmp_path / "tests"
    tests.mkdir()
    filenames = [
        "test_a.py", "test_b.py", "test_c_native.py", "test_d.py",
        "test_e.py", "test_f_native.py", "test_g.py",
    ]
    files = [tests / name for name in filenames]
    for path in files:
        path.touch()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "_INDEPENDENT_SUBSUITE_FILES", {
        "fixture": {"test_c_native.py", "test_f_native.py"},
    })
    groups = runner._test_file_groups("fixture", 2, filtered=filtered)
    assert [path for group in groups for path in group] == files
    assert len({path for group in groups for path in group}) == len(files)
    if filtered:
        assert groups == [files]
    else:
        assert groups == [files[:2], [files[2]], files[3:5], [files[5]], [files[6]]]


def test_missing_independent_native_group_fails_explicitly(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "_INDEPENDENT_SUBSUITE_FILES", {"fixture": {"test_missing.py"}})
    with pytest.raises(ValueError, match="Independent sub-suite files are missing"):
        runner._test_file_groups("fixture", 25)


def test_explicit_collection_does_not_require_unselected_independent_files(tmp_path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    selected = tests / "test_selected.py"
    selected.touch()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "_INDEPENDENT_SUBSUITE_FILES", {"fixture": {"test_native.py"}})
    assert runner._test_file_groups(
        "fixture", 25, test_files=("tests/test_selected.py",),
    ) == [[selected]]


def test_worker_launch_uses_canonical_headlessness(monkeypatch, tmp_path):
    ready = tmp_path / "ready"
    ready.touch()
    calls = []
    monkeypatch.setattr(containment, "no_window_kwargs", lambda: {"creationflags": 123})

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(containment.subprocess, "run", run)
    assert containment._worker_main([str(ready), "command", "argument"]) == 7
    assert calls == [
        (
            ["command", "argument"],
            {"check": False, "creationflags": 123, **containment._windows_stdio_kwargs()},
        )
    ]


@pytest.mark.parametrize("has_project", [False, True])
def test_fresh_preparation_attributes_repository_sources(monkeypatch, tmp_path, has_project):
    plugin = tmp_path / "plugins" / "example"
    plugin.mkdir(parents=True)
    if has_project:
        (plugin / "pyproject.toml").write_text(
            '[build-system]\nrequires=[]\nbuild-backend="setuptools.build_meta"\n'
            '[project]\nname="example"\nversion="1.0"\ndependencies=["owned-lib"]\n'
            '[tool.uv.sources]\nowned-lib={path="../../libs/owned"}\n'
            '[tool.setuptools.packages.find]\nwhere=["src"]\n',
            encoding="utf-8",
        )
        (plugin / "src").mkdir()
        library = tmp_path / "libs" / "owned"
        library.mkdir(parents=True)
        (library / "pyproject.toml").write_text(
            '[project]\nname="owned-lib"\nversion="1.0"\n', encoding="utf-8",
        )
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "PLUGINS", tmp_path / "plugins")
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "environments")
    monkeypatch.setattr(runner, "_dep_fingerprint", lambda name: "fixture")
    environment = runner.VENV_ROOT / "example"
    environment.mkdir(parents=True)
    python = runner._venv_python("example")
    admitted = runner.run_uv_install
    installs = []
    probes = []

    def prepare(uv, arguments, **kwargs):
        assert kwargs["trusted_source_roots"] == (tmp_path,)
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
    assert runner._ensure_venv("example", "uv", reinstall=False) == python
    assert len(probes) == 2 and len(installs) == 1
    assert "--exclude-newer" in installs[0] and "--reinstall" in installs[0]
    assert ("-e" in installs[0]) is has_project
    assert (environment / ".dep-fingerprint").read_text() == "fixture"


@pytest.mark.skipif(os.name == "nt", reason="POSIX creation permissions")
def test_pytest_group_parent_is_created_without_shared_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *_args, **_kwargs: Path(sys.executable))
    monkeypatch.setattr(runner, "_test_file_groups", lambda *_args, **_kwargs: [[]])
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    observed: list[int] = []

    def capture(command: list[str], **_kwargs: object) -> int:
        import stat

        directory = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--basetemp="))).parent
        observed.append(stat.S_IMODE(directory.stat().st_mode))
        return 0

    monkeypatch.setattr(runner, "run_contained", capture)
    previous = os.umask(0o002)
    try:
        assert runner.run_plugin(
            "example", "unused", reinstall=False, kexpr=None,
            limits=containment.Limits(), plugin_timeout=60,
            test_timeout=10, max_files_per_subsuite=25,
        ) == 0
    finally:
        os.umask(previous)
    assert observed == [0o700]


def test_containment_owner_launch_preserves_process_contract(monkeypatch, tmp_path):
    captured = {}

    class FinishedProcess:
        pid = 123

        def poll(self):
            return 17

        def wait(self, *, timeout):
            return 17

    def popen(command, **kwargs):
        captured.update(kwargs)
        assert command == containment._worker_command(
            ["command"], tmp_path / ".containment-ready"
        )
        return FinishedProcess()

    class Job:
        def __init__(self, limits):
            pass

        def assign(self, pid):
            assert pid == 123

        def close(self):
            captured["job_closed"] = True

    monkeypatch.setattr(containment, "no_window_kwargs", lambda: {"creationflags": 123})
    monkeypatch.setattr(containment.subprocess, "Popen", popen)
    monkeypatch.setattr(containment, "_WindowsJob", Job)
    monkeypatch.setattr(containment, "_terminate_posix_group", lambda *args: None)
    monkeypatch.setattr(containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(containment, "_posix_exit_code", lambda fd: 17)
    assert containment._run_contained_process(
        ["command"], cwd=tmp_path, env={"TEST": "value"}, sandbox=tmp_path,
        limits=containment.Limits(),
    ) == 17
    assert captured["creationflags"] == 123
    assert captured["cwd"] == str(tmp_path)
    expected_environment = {"TEST": "value"}
    if os.name != "nt":
        assert len(captured["pass_fds"]) == 2
        expected_environment["COPILOT_EXTENSIONS_CONTAINMENT_RESULT_FD"] = str(
            captured["pass_fds"][0]
        )
        expected_environment["COPILOT_EXTENSIONS_CONTAINMENT_OWNER_FD"] = str(
            captured["pass_fds"][1]
        )
    assert captured["env"] == expected_environment
    assert {
        name: captured[name] for name in containment._windows_stdio_kwargs()
    } == containment._windows_stdio_kwargs()
    assert captured["start_new_session"] == (os.name != "nt")
    if os.name == "nt":
        assert captured["job_closed"]
    assert not (tmp_path / ".containment-ready").exists()


@pytest.mark.skipif(os.name != "nt", reason="real Windows console inheritance")
@pytest.mark.parametrize("timeout", [False, True], ids=["exit-and-streams", "timeout-tree"])
def test_windowless_runner_contains_bare_console_descendants(tmp_path, timeout, monkeypatch):
    """Exercise pythonw -> runner -> worker -> bare Python/PowerShell/git.

    No subprocess patch is installed: intentional CREATE_NEW_CONSOLE callers
    remain untouched. Console handles/visibility are OS observations, not flag
    assertions. The opt-in desktop lane additionally observes external terminal
    hosts, visible windows and foreground changes against a pre-launch baseline.
    """
    import ctypes
    from ctypes import wintypes

    from agent_procutil import no_window_kwargs, windowless_python

    pythonw = windowless_python(sys.executable)
    if Path(pythonw).name.lower() != "pythonw.exe":
        pytest.skip("windowless base interpreter unavailable")
    powershell = shutil.which("powershell.exe")
    git = shutil.which("git.exe")
    if not powershell or not git:
        pytest.skip("PowerShell and git are required console representatives")
    receipt = tmp_path / "descendants.json"
    result = tmp_path / "result.json"
    sandbox = tmp_path / "sandbox"
    shell_timeout = 30
    child_timeout = 2 * shell_timeout + 10
    wall_seconds = 5 if timeout else child_timeout + 10
    owner_timeout = max(25, wall_seconds + 10)
    launcher_timeout = owner_timeout + 5
    console_probe = """
import ctypes, os
k = ctypes.windll.kernel32
k.GetConsoleWindow.restype = ctypes.c_void_p
window = k.GetConsoleWindow()
assert not window or not ctypes.windll.user32.IsWindowVisible(window)
"""
    ps_script = (
        'Add-Type \'using System; using System.Runtime.InteropServices; '
        'public class ConsoleProbe { [DllImport("kernel32.dll")] '
        'public static extern IntPtr GetConsoleWindow(); }\'; '
        'if ([ConsoleProbe]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 9 }; '
        f"& '{git.replace(chr(39), chr(39) * 2)}' --version; "
        '[Console]::Error.WriteLine("nested-stderr"); exit 7'
    )
    child = console_probe + f"""
import json, subprocess, sys, time
from pathlib import Path
assert os.environ['COPILOT_EXTENSIONS_TEST_CONTAINED'] == '1'
assert Path.cwd() == Path({str(tmp_path)!r})
Path({str(receipt)!r}).write_text(json.dumps({{
    'pid': os.getpid(), 'window': window,
}}))
if {timeout!r}:
    time.sleep(120)
else:
    for cycle in range(2):
        probe = subprocess.run(
            [{powershell!r}, '-NoProfile', '-NonInteractive', '-Command', {ps_script!r}],
            capture_output=True, text=True, timeout={shell_timeout},
        )
        assert probe.returncode == 7, (probe.returncode, probe.stdout, probe.stderr)
        assert 'git version' in probe.stdout
        assert 'nested-stderr' in probe.stderr
    print('nested-stdout')
    print('nested-stderr', file=sys.stderr)
    sys.exit(7)
"""
    command = console_probe + f"""
import subprocess, sys
assert sys.stdin.readline().strip() == 'nested-input'
probe = subprocess.run([sys.executable, '-c', {child!r}], timeout={child_timeout})
sys.exit(17 if probe.returncode == 7 else 18)
"""
    owner = textwrap.dedent(f"""
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, {str(SCRIPT.parent)!r})
        import plugin_test_containment as containment
        sandbox = Path({str(sandbox)!r})
        env = containment.isolated_environment(os.environ, sandbox)
        rc = containment.run_contained(
            [{sys.executable!r}, '-c', {command!r}],
            cwd=Path({str(tmp_path)!r}), env=env, sandbox=sandbox,
            limits=containment.Limits(wall_seconds={wall_seconds},
                                      poll_seconds=0.05, max_processes=32,
                                      max_memory_mb=512, max_temp_mb=32),
        )
        Path({str(result)!r}).write_text(json.dumps({{'returncode': rc}}))
    """)
    streams = tmp_path / "streams.json"
    launcher = f"""
import ctypes, json, subprocess, sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPT.parent)!r})
from plugin_test_containment import no_window_kwargs
assert not ctypes.windll.kernel32.GetConsoleWindow()
probe = subprocess.run(
    [{sys.executable!r}, '-c', {owner!r}],
    input='nested-input\\n', capture_output=True, text=True,
    timeout={owner_timeout}, **no_window_kwargs(),
)
Path({str(streams)!r}).write_text(json.dumps({{
    'returncode': probe.returncode, 'stdout': probe.stdout, 'stderr': probe.stderr,
}}))
"""
    desktop = os.environ.get("MUX_CHILD_WINDOWS_E2E") == "1"
    stop = threading.Event()
    visible: set[int] = set()
    focused: set[int] = set()
    owned: set[int] = set()
    roots: set[int] = set()
    if desktop:
        session = wintypes.DWORD()
        assert ctypes.windll.kernel32.ProcessIdToSessionId(
            os.getpid(), ctypes.byref(session),
        )
        assert session.value != 0, "desktop observation cannot run in Windows session zero"
        monkeypatch.syspath_prepend(
            str(SCRIPT.parent.parent / "worktree-manager" / "tests"),
        )
        from mux_windows_observer import descendants, processes, windows

        baseline_windows, baseline_foreground = windows()
        baseline_pids = set(processes())
        real_popen = subprocess.Popen

        def popen(argv, **kwargs):
            process = real_popen(argv, **kwargs)
            roots.add(process.pid)
            return process

        monkeypatch.setattr(subprocess, "Popen", popen)

        def observe() -> None:
            while not stop.is_set():
                table = processes()
                for pid in roots.copy():
                    owned.update(descendants(pid, table))
                current_windows, foreground_pid = windows()
                terminals = {"windowsterminal.exe", "openconsole.exe", "conhost.exe"}
                for hwnd, pid in current_windows.items():
                    if pid in owned or (
                        hwnd not in baseline_windows
                        and table.get(pid, ("", 0))[0].lower() in terminals
                    ):
                        visible.add(hwnd)
                if foreground_pid in owned or (
                    foreground_pid != baseline_foreground
                    and table.get(foreground_pid, ("", 0))[0].lower() in terminals
                ):
                    focused.add(foreground_pid)
                for pid, (name, _) in table.items():
                    if pid not in baseline_pids and name.lower() == "openconsole.exe":
                        visible.add(pid)
                stop.wait(.01)

    with ThreadPoolExecutor(max_workers=1) as executor:
        monitor = executor.submit(observe) if desktop else None
        try:
            completed = subprocess.run(
                [pythonw, "-c", launcher], cwd=tmp_path, capture_output=True,
                text=True, timeout=launcher_timeout, **no_window_kwargs(),
            )
            if desktop:
                assert roots and owned, "observer never identified runner fixture processes"
                deadline = time.monotonic() + 5
                while owned & processes().keys() and time.monotonic() < deadline:
                    time.sleep(.02)
                assert not (owned & processes().keys()), "runner fixture descendants leaked"
        finally:
            stop.set()
            if monitor is not None:
                monitor.result(timeout=3)
    assert not visible, "runner child or external terminal host surfaced a window"
    assert not focused, "runner child or external terminal host stole focus"
    assert completed.returncode == 0, completed.stderr
    captured = json.loads(streams.read_text())
    assert captured["returncode"] == 0, captured["stderr"]
    assert json.loads(result.read_text())["returncode"] == (124 if timeout else 17), captured
    identity = json.loads(receipt.read_text())
    assert not identity["window"]
    if timeout:
        assert "wall-clock limit exceeded" in captured["stderr"]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x00100000, False, identity["pid"])
        if handle:
            try:
                assert kernel32.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel32.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87  # PID no longer exists.
    else:
        assert "nested-stdout" in captured["stdout"]
        assert "nested-stderr" in captured["stderr"]


def test_default_environment_redirects_all_mutable_roots(tmp_path: Path) -> None:
    env = containment.isolated_environment(
        {
            "PATH": os.environ.get("PATH", ""),
            "HOME": "/real/home",
            "AGENT_WORKTREES_HOME": "/real/agent-worktrees",
            "AGENT_WORKTREES_PROJECTS_YAML": "/real/projects.yaml",
            "GH_TOKEN": "not-a-real-token",
            "COPILOT_AGENT_SESSION_ID": "live-session",
            "AGENT_WORKTREES_OWNER_REF": "live-owner",
        },
        tmp_path,
    )

    assert env["PATH"] == os.environ.get("PATH", "")
    assert "GH_TOKEN" not in env
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "AGENT_WORKTREES_OWNER_REF" not in env
    for name in containment.ROOT_ENV_NAMES:
        Path(env[name]).resolve().relative_to(tmp_path.resolve())
    assert "AGENT_CONTAINERS_CONFIG" not in env
    Path(env["AGENT_WORKTREES_PROJECTS_YAML"]).resolve().relative_to(
        tmp_path.resolve()
    )


def test_external_targets_are_explicit_and_contained_in_repository(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    assert runner._plugin_dir("worktree-manager") == tmp_path / "worktree-manager"
    assert runner._plugin_dir("payload-invocation") == tmp_path / "libs" / "payload-invocation"
    assert runner._plugin_dir("runtime-source") == tmp_path / "libs" / "versioned-runtime"
    assert runner._plugin_dir("worktree-identity") == tmp_path / "libs" / "worktree-identity"
    assert runner._plugin_dir("repository-policy") == tmp_path / "libs" / "repository-policy"
    assert runner.EXTERNAL_TEST_DEPENDENCIES["repository-policy"] == ("pyyaml>=6.0.3",)
    assert runner.EXTERNAL_TEST_DEPENDENCIES["payload-invocation"] == ("pyyaml>=6.0.3",)
    assert runner.EXTERNAL_TEST_DEPENDENCIES["runtime-source"] == ("pyyaml>=6.0.3",)


def test_optional_file_override_is_validated_only_when_present(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(os.environ, tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in containment.OPTIONAL_FILE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    portfolio_guard.validate_contained_environment()

    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(tmp_path.parent / "config.yaml"))
    with pytest.raises(pytest.UsageError, match="AGENT_CONTAINERS_CONFIG"):
        portfolio_guard.validate_contained_environment()


def test_host_state_opt_in_preserves_credentials_not_session_affinity(
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(
        {
            "HOME": "/real/home",
            "GH_TOKEN": "not-a-real-token",
            "COPILOT_AGENT_SESSION_ID": "live-session",
            "AGENT_WORKTREES_OWNER_REF": "live-owner",
        },
        tmp_path,
        allow_explicit_tiers=True,
        allow_host_state=True,
    )

    assert env["HOME"] == "/real/home"
    assert env["GH_TOKEN"] == "not-a-real-token"
    assert env[containment.ALLOW_HOST_STATE_ENV] == "1"
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "AGENT_WORKTREES_OWNER_REF" not in env
    for name in containment.ALWAYS_SANDBOX_ENV_NAMES:
        Path(env[name]).resolve().relative_to(tmp_path.resolve())


def test_host_state_requires_explicit_tiers_at_environment_boundary(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="allow_host_state requires allow_explicit_tiers",
    ):
        containment.isolated_environment(
            os.environ,
            tmp_path,
            allow_host_state=True,
        )


def test_host_state_still_fails_closed_on_escaped_temp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(
        os.environ,
        tmp_path,
        allow_explicit_tiers=True,
        allow_host_state=True,
    )
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TMPDIR", str(tmp_path.parent))

    with pytest.raises(pytest.UsageError, match="TMPDIR"):
        portfolio_guard.validate_contained_environment()


def test_admission_fails_fast_with_live_holder(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "environment").mkdir()
    class BusyLease:
        def acquire(self) -> None:
            raise runner.AlreadyRunningError(tmp_path / "runner.lock", 123)

    monkeypatch.setattr(admission, "SingleInstance", lambda *_args, **_kwargs: BusyLease())

    with pytest.raises(runner.AlreadyRunningError) as exc:
        runner._acquire_admission(0, tmp_path / "environment")
    assert exc.value.holder_pid == 123


def test_admission_wait_is_bounded_and_retries(monkeypatch) -> None:
    monkeypatch.setattr(admission, "admission_dir", lambda _: Path("unused-lock"))
    class EventuallyAvailableLease:
        calls = 0

        def acquire(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise runner.AlreadyRunningError(Path("runner.lock"), 123)

    lease = EventuallyAvailableLease()
    clock = iter((10.0, 10.0))
    monkeypatch.setattr(admission, "SingleInstance", lambda *_args, **_kwargs: lease)
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    assert runner._acquire_admission(1.0, Path("environment")) is lease
    assert lease.calls == 2


def test_run_holds_one_environment_lease_at_a_time(monkeypatch) -> None:
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda _wait, root: events.append(f"acquire:{root.name}") or Lease(),
    )
    monkeypatch.setattr(
        runner,
        "run_plugin",
        lambda name, *_args, **_kwargs: events.append(f"run:{name}") or 0,
    )

    assert runner.main(["alpha", "beta"]) == 0
    assert events == ["acquire:alpha", "run:alpha", "release", "acquire:beta", "run:beta", "release"]


def test_unexpected_runner_error_does_not_wedge_remaining_plugins(monkeypatch) -> None:
    """Regression (coverage-guided-ci full-matrix local validation pass,
    2026-10-05): a single plugin's own contained-run failure must never
    propagate past the per-plugin loop and abort the rest of ``--all``/
    ``--changed`` -- only ``ContainmentError``/``CalledProcessError`` were
    ever caught here, so any OTHER exception type (e.g. a transient
    OS-level resource issue after many sequential heavy runs) would
    silently truncate validation of every plugin after the one that
    raised it."""
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    def _run_plugin(name: str, *_args, **_kwargs) -> int:
        if name == "beta":
            raise MemoryError("simulated unexpected failure, not ContainmentError")
        events.append(f"run:{name}")
        return 0

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda _wait, root: events.append(f"acquire:{root.name}") or Lease(),
    )
    monkeypatch.setattr(runner, "run_plugin", _run_plugin)

    # Exit code 1 (a real failure was recorded), but every target was
    # still attempted -- "gamma" (after the raising "beta") must have run.
    assert runner.main(["alpha", "beta", "gamma"]) == 1
    assert events == [
        "acquire:alpha", "run:alpha", "release",
        "acquire:beta", "release", "acquire:gamma", "run:gamma", "release",
    ]


def test_guards_also_take_environment_admission(monkeypatch) -> None:
    # `--guards` still reaches `_ensure_venv()` and so can rebuild/delete
    # the SHARED on-disk venv a concurrent bare admitted run may be
    # relying on mid-execution -- it is not exempt.
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    acquire_calls: list[float] = []
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda wait, root: acquire_calls.append(wait) or Lease(),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--guards"]) == 0
    assert acquire_calls == [0.0]


def test_collect_only_also_takes_environment_admission(monkeypatch) -> None:
    # `--collect-only` likewise reaches `_ensure_venv()` and so can
    # rebuild/delete the SHARED on-disk venv -- it is not exempt either.
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    acquire_calls: list[float] = []
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda wait, root: acquire_calls.append(wait) or Lease(),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--collect-only"]) == 0
    assert acquire_calls == [0.0]

@pytest.mark.parametrize("explicit", [False, True])
def test_native_owner_test_profile_is_scoped_and_explicit_limits_win(monkeypatch, explicit):
    class Lease:
        def release(self):
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(runner, "_acquire_admission", lambda wait, root: Lease())
    observed = {}

    def capture(name, _uv, **kwargs):
        observed[name] = (
            kwargs["limits"].wall_seconds, kwargs["plugin_timeout"], kwargs["test_timeout"],
            kwargs["max_files_per_subsuite"],
        )
        return 0

    monkeypatch.setattr(runner, "run_plugin", capture)
    arguments = ["agent-pull-requests", "example"]
    if explicit:
        arguments += [
            "--timeout", "45", "--plugin-timeout", "75", "--test-timeout", "9",
            "--max-files-per-sub-suite", "3",
        ]
    assert runner.main(arguments) == 0
    if explicit:
        assert observed == {"agent-pull-requests": (45, 75, 9, 3), "example": (45, 75, 9, 3)}
    else:
        expected = (1200, 1800, 30) if sys.platform == "win32" else (300, 900, 30)
        assert observed == {"agent-pull-requests": (*expected, 10), "example": (300, 900, 30, 25)}


@pytest.mark.parametrize("size", [0, -1])
def test_explicit_file_group_limit_must_remain_positive(size):
    with pytest.raises(SystemExit) as error:
        runner.main(["agent-pull-requests", "--max-files-per-sub-suite", str(size)])
    assert error.value.code == 2


def test_prepare_only_takes_environment_admission(monkeypatch) -> None:
    # Like --guards/--collect-only, --prepare-only is NOT exempt: it can
    # rebuild/delete the SHARED on-disk venv a concurrent bare admitted
    # run may be relying on mid-execution.
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    acquire_calls: list[float] = []
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda wait, root: acquire_calls.append(wait) or Lease(),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--prepare-only"]) == 0
    assert acquire_calls == [0.0]


def test_run_plugin_prepare_only_never_invokes_pytest(monkeypatch, tmp_path: Path) -> None:
    """The whole point of `--prepare-only`: it must build/update the venv
    and return WITHOUT ever importing a single test module or
    `conftest.py` -- unlike `--collect-only`, which still runs pytest's
    own collection (and therefore executes that module-level code)."""
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    ensure_venv_calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        runner, "_ensure_venv",
        lambda name, uv, *, reinstall: ensure_venv_calls.append((name, reinstall)) or Path("fake-py"),
    )
    monkeypatch.setattr(
        runner, "run_contained",
        lambda *_a, **_k: pytest.fail("--prepare-only must never invoke pytest"),
    )

    rc = runner.run_plugin(
        "alpha", "uv",
        reinstall=False, kexpr=None, limits=runner.Limits(
            wall_seconds=60, max_processes=10, max_memory_mb=100,
            max_temp_mb=100, poll_seconds=0.1,
        ),
        plugin_timeout=60.0, test_timeout=10.0, max_files_per_subsuite=25,
        prepare_only=True,
    )
    assert rc == 0
    assert ensure_venv_calls == [("alpha", False)]


def test_host_state_requires_explicit_tier_opt_in(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        runner.main(["--allow-host-state"])
    assert exc.value.code == 2
    assert "--allow-host-state requires --allow-explicit-tiers" in capsys.readouterr().err


def test_admission_wait_rejects_infinite_value(monkeypatch, capsys) -> None:
    # `float("inf")` parses cleanly and isn't `< 0`, so it would otherwise
    # slip past a bare negative check and poll forever under contention,
    # defeating the documented bounded-wait contract.
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    with pytest.raises(SystemExit) as exc:
        runner.main(["alpha", "--admission-wait", "inf"])
    assert exc.value.code == 2
    assert "admission_wait must be a non-negative, finite number" in capsys.readouterr().err


def test_admission_wait_rejects_nan_value(monkeypatch, capsys) -> None:
    # `float("nan")` also parses cleanly, and `nan < 0` is False, so a
    # bare negative check alone would silently accept it too.
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    with pytest.raises(SystemExit) as exc:
        runner.main(["alpha", "--admission-wait", "nan"])
    assert exc.value.code == 2
    assert "admission_wait must be a non-negative, finite number" in capsys.readouterr().err



class _FakeCompletedProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_changed_plugins_always_includes_cross_plugin_contract_testers(monkeypatch) -> None:
    """A PR touching only plugin 'foo' must still schedule any registered
    cross-plugin contract tester (regression: ThomasMichon/copilot-extensions
    #4166 broke copilot-extensions-harness's own marketplace-wide contract
    test without touching a single file under that plugin, so the PR's own
    --changed-scoped CI never ran it)."""
    calls = iter([
        _FakeCompletedProcess("plugins/foo/src/foo.py\n"),
        _FakeCompletedProcess(""),
    ])
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: next(calls))
    monkeypatch.setattr(
        runner, "_has_suite",
        lambda name: name in {"foo", "copilot-extensions-harness", "customizing-copilot"},
    )
    result = runner.changed_plugins("origin/main")
    assert result == sorted({"foo", "copilot-extensions-harness", "customizing-copilot"})


def test_changed_plugins_empty_diff_schedules_nothing(monkeypatch) -> None:
    """No real change (e.g. a docs-only or non-plugin diff) must not force
    the contract testers to run either -- only an actual plugin change
    should pull them in."""
    calls = iter([
        _FakeCompletedProcess(""),
        _FakeCompletedProcess(""),
    ])
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: next(calls))
    monkeypatch.setattr(runner, "_has_suite", lambda name: True)
    assert runner.changed_plugins("origin/main") == []
