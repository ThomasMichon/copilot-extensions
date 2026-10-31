"""Explicit native-policy RED witnesses for fresh Manager engine repair."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import textwrap
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent_procutil import no_window_kwargs

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins/agent-worktrees"
pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="real installer/lifecycle policy lane; not promotion-unit evidence",
)
REAL_RUN = subprocess.run


def _copy_fixture_tree(source, destination, **options):
    if os.name != "nt":
        return shutil.copytree(source, destination, **options)
    import ctypes
    from ctypes import wintypes

    native = ctypes.WinDLL("kernel32", use_last_error=True).CopyFileW
    native.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.BOOL]
    native.restype = wintypes.BOOL
    pending = []
    directories = []
    ignore = options.pop("ignore", None)

    def defer_file(source, destination):
        pending.append((source, destination))
        return destination

    def remember_directory(directory, names):
        directories.append((
            directory, Path(destination) / Path(directory).relative_to(source),
        ))
        return ignore(directory, names) if ignore else []

    def copy_file(pair):
        origin, target = pair
        if not native(os.fspath(origin), os.fspath(target), False):
            return (origin, target, str(ctypes.WinError(ctypes.get_last_error())))
        return None

    result = shutil.copytree(
        source, destination, copy_function=defer_file, ignore=remember_directory, **options,
    )
    with ThreadPoolExecutor(max_workers=4) as copies:
        errors = [error for error in copies.map(copy_file, pending) if error]
    if errors:
        raise shutil.Error(errors)
    # Deferred file creation changes directory times; restore them only after joining.
    for origin, target in reversed(directories):
        shutil.copystat(origin, target)
    return result


def _remove_fixture_tree(root: Path) -> None:
    if os.name != "nt":
        shutil.rmtree(root)
        return
    script = textwrap.dedent("""
        import os, stat, sys
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path

        directories, files = [], []

        def collect(path):
            state = path.lstat()
            if state.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                if state.st_file_attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                    path.rmdir()
                else:
                    path.unlink()
                return
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        collect(Path(entry.path))
                    else:
                        files.append(entry.path)
            directories.append(path)

        collect(Path(sys.argv[1]))
        with ThreadPoolExecutor(max_workers=4) as removals:
            for _ in removals.map(os.unlink, files):
                pass
        for directory in directories:
            directory.rmdir()
    """)
    environment = dict(os.environ)
    for name in ("TMP", "TEMP", "TMPDIR"):
        environment[name] = str(root.parent)
    try:
        subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", script, str(root)],
            cwd=root.parent, env=environment,
            capture_output=True, text=True, check=True, timeout=10, **no_window_kwargs(),
        )
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr, end="", flush=True)
        raise


def test_repair_fixture_removal_keeps_link_target(tmp_path):
    root = tmp_path / "fixture [literal] \u00e9"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "payload").write_bytes(b"fixture")
    (root / "empty").mkdir()
    target = tmp_path / "outside-fixture"
    target.mkdir()
    sentinel = target / "preserved"
    sentinel.write_bytes(b"outside")
    try:
        (root / "link").symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symbolic links unavailable: {error}")
    _remove_fixture_tree(root)
    assert not root.exists()
    assert sentinel.read_bytes() == b"outside"


def test_repair_fixture_removal_does_not_hide_missing_root(tmp_path):
    with pytest.raises((FileNotFoundError, subprocess.CalledProcessError)):
        _remove_fixture_tree(tmp_path / "missing")


def test_repair_fixture_removal_relocates_its_own_temporary_directory(tmp_path, monkeypatch):
    root = tmp_path / "fixture"
    temporary = root / "tmp"
    temporary.mkdir(parents=True)
    (temporary / "file").write_bytes(b"fixture")
    for name in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(name, str(temporary))
    _remove_fixture_tree(root)
    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows fixture root-link cleanup")
def test_repair_fixture_removal_keeps_target_when_root_is_link(tmp_path):
    target = tmp_path / "outside-fixture"
    target.mkdir()
    sentinel = target / "preserved"
    sentinel.write_bytes(b"outside")
    root = tmp_path / "linked-fixture"
    try:
        root.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symbolic-link privilege unavailable")
        raise
    _remove_fixture_tree(root)
    assert not os.path.lexists(root)
    assert sentinel.read_bytes() == b"outside"


def test_repair_fixture_removal_surfaces_worker_failure(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows bounded fixture removal")
    root = tmp_path / "fixture"
    root.mkdir()
    blocked = root / "blocked"
    blocked.write_bytes(b"retained")
    blocked.chmod(0o400)
    try:
        with pytest.raises(subprocess.CalledProcessError) as error:
            _remove_fixture_tree(root)
        assert "PermissionError" in error.value.stderr
        assert blocked.read_bytes() == b"retained"
    finally:
        blocked.chmod(0o600)


def test_repair_fixture_copy_preserves_content_and_metadata(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = source / "fixture caf\u00e9.txt"
    data = bytes(range(256))
    original.write_bytes(data)
    os.utime(original, ns=(1234567890000000000, 1234567890123456700))
    before = original.stat()
    copied = tmp_path / "copy with spaces"
    _copy_fixture_tree(source, copied, symlinks=True)
    actual = copied / original.name
    assert actual.read_bytes() == data == original.read_bytes()
    assert actual.stat().st_mtime_ns == before.st_mtime_ns
    assert actual.stat().st_mode == before.st_mode
    assert original.stat().st_mtime_ns == before.st_mtime_ns


def test_repair_fixture_copy_does_not_hide_native_failure(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = source / "removed-after-enumeration"
    original.write_text("fixture")

    def remove_before_copy(directory, names):
        original.unlink()
        return []

    with pytest.raises(shutil.Error, match="removed-after-enumeration"):
        _copy_fixture_tree(source, tmp_path / "copy", ignore=remove_before_copy)
    assert not (tmp_path / "copy" / original.name).exists()


def test_repair_fixture_copy_preserves_tree_and_independent_files(tmp_path):
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "empty").mkdir()
    original = nested / "payload"
    original.write_bytes(b"complete fixture")
    (source / "ignored").write_bytes(b"not selected")
    os.utime(nested, ns=(1234567890000000000, 1234567890123456700))
    before = nested.stat()
    destination = tmp_path / "copy"
    (destination / "nested").mkdir(parents=True)
    (destination / "nested" / "payload").write_bytes(b"old")
    (destination / "unrelated").write_bytes(b"retained")

    _copy_fixture_tree(
        source, destination, ignore=shutil.ignore_patterns("ignored"),
        symlinks=True, dirs_exist_ok=True,
    )

    copied = destination / "nested" / "payload"
    assert copied.read_bytes() == b"complete fixture"
    assert (destination / "empty").is_dir()
    assert not (destination / "ignored").exists()
    assert (destination / "unrelated").read_bytes() == b"retained"
    assert (destination / "nested").stat().st_mtime_ns == before.st_mtime_ns
    assert (destination / "nested").stat().st_mode == before.st_mode
    copied.write_bytes(b"independent")
    assert original.read_bytes() == b"complete fixture"


def test_repair_fixture_copy_preserves_links(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_bytes(b"not copied through link")
    try:
        (source / "directory-link").symlink_to(target, target_is_directory=True)
        (source / "file-link").symlink_to(sentinel)
    except OSError as error:
        pytest.skip(f"symbolic links unavailable: {error}")
    destination = tmp_path / "copy"
    _copy_fixture_tree(source, destination, symlinks=True)
    assert (destination / "directory-link").is_symlink()
    assert (destination / "file-link").is_symlink()
    assert os.readlink(destination / "directory-link") == os.readlink(source / "directory-link")
    assert os.readlink(destination / "file-link") == os.readlink(source / "file-link")
    assert sentinel.read_bytes() == b"not copied through link"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repair_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
):
    marketplace = getattr(request, "param", "fixture")
    # This module is intentionally independent of _conftest_sandbox's roster.
    # No source/lifecycle capability from the driving session may enter it.
    for name in tuple(os.environ):
        if name.startswith(("COPILOT_", "CLAUDE_", "AGENT_", "WORKTREE_MANAGER_", "GIT_", "UV_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV"):
        monkeypatch.delenv(name, raising=False)
    previous_cwd = Path.cwd()
    temporary = ExitStack()
    request.addfinalizer(temporary.close)
    if os.name == "nt":
        root = tmp_path.parent / ("er-" + secrets.token_hex(4))
        if os.path.lexists(root):
            raise FileExistsError(f"Private fixture already exists: {root}")
        leases = _load(
            REPO / "libs/versioned-runtime/versioned_runtime_leases.py",
            "engine_repair_fixture_leases",
        )
        descriptor = leases._windows_private_lease_file(tmp_path.parent, root, "root-fixture")
        temporary.callback(_remove_fixture_tree, root)
        os.close(descriptor)
    else:
        root = Path(temporary.enter_context(
            tempfile.TemporaryDirectory(prefix="er-", dir=tmp_path.parent),
        ))
    home = root / "h"
    home.mkdir()
    runtime = home / ".agent-worktrees"
    runtime.mkdir()
    manager = home / ".worktree-manager"
    manager.mkdir()
    tools = root / "t"
    tools.mkdir()
    for key, value in {
        "HOME": home, "USERPROFILE": home, "AGENT_HOME": home,
        "WORKTREE_MANAGER_ROOT": manager,
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR": runtime / "control-plane-providers.d",
        "XDG_CONFIG_HOME": home / ".config", "APPDATA": home / "AppData/Roaming",
        "LOCALAPPDATA": home / "AppData/Local", "PROGRAMDATA": root / "programdata",
        "TMPDIR": root / "tmp", "TEMP": root / "tmp", "TMP": root / "tmp",
    }.items():
        Path(value).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(value))
    monkeypatch.setattr(tempfile, "tempdir", str(root / "tmp"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(home)
    temporary.callback(os.chdir, previous_cwd)
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://index.invalid/simple")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    empty_config = root / "gitconfig"
    empty_config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    effect = root / "construction.json"
    monkeypatch.setenv("ENGINE_REPAIR_CONSTRUCTION_FENCE", str(effect))
    monkeypatch.setenv("ENGINE_REPAIR_FORBIDDEN_UV", str(root / "uv-called"))
    if os.name == "nt":
        (tools / "uv.cmd").write_text(
            '@echo off\r\nif "%~1"=="--version" if "%~2"=="" (\r\n'
            'echo uv 0.8.22\r\nexit /b 0\r\n)\r\n'
            '(>> "%ENGINE_REPAIR_FORBIDDEN_UV%" echo %*)\r\nexit /b 96\r\n',
        )
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    else:
        uv = tools / "uv"
        uv.write_text(
            '#!/bin/sh\nif [ "$#" = 1 ] && [ "$1" = --version ]; then\n'
            "printf 'uv 0.8.22\\n'\nexit 0\nfi\n"
            'printf "%s\\n" "$*" > "$ENGINE_REPAIR_FORBIDDEN_UV"\nexit 96\n'
        )
        uv.chmod(0o700)
        shell = shutil.which("bash")
    if not shell:
        temporary.close()
        pytest.skip("matching native installer shell unavailable")
    if not shutil.which("git"):
        temporary.close()
        pytest.skip("real installer prerequisite Git unavailable")
    source = manager / "staging"
    fresh = source / "plugins/agent-worktrees"
    ignore = shutil.ignore_patterns(
        "tests", "__pycache__", "*.pyc", "*.pyo", ".git", ".test-venvs", "*.egg-info",
    )
    copies = temporary.enter_context(ThreadPoolExecutor(max_workers=2))
    source_copies = [
        copies.submit(_copy_fixture_tree, PLUGIN, fresh, ignore=ignore, symlinks=True),
        copies.submit(
            _copy_fixture_tree, REPO / "libs", source / "libs", ignore=ignore, symlinks=True,
        ),
    ]
    for future in source_copies:
        future.result()
    policy = source / "worktree-manager/scripts"
    policy.mkdir(parents=True)
    shutil.copy2(REPO / "worktree-manager/scripts/update_entry.py", policy / "update_entry.py")
    roster = source / ".github/plugin"
    roster.mkdir(parents=True)
    shutil.copy2(REPO / ".github/plugin/marketplace.json", roster / "marketplace.json")
    profile = _load(
        PLUGIN / "tests/runtime_install_fixture.py", "engine_repair_fixture_profile",
    )
    # The established fixture helper redirects copied OS-account lookups only;
    # all canonical source, owner, lifecycle and installer-action policy stays real.
    profile.isolate_account_profile(fresh, home)
    actual = fresh / "scripts/runtime_install_real.py"
    shutil.copy2(fresh / "scripts/runtime_install.py", actual)
    (fresh / "scripts/runtime_install.py").write_text(
        "import json, os, pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))\n"
        "import runtime_install_real as real\n"
        "def construction_fence(*args, **kwargs):\n"
        "    pathlib.Path(os.environ['ENGINE_REPAIR_CONSTRUCTION_FENCE']).write_text(\n"
        "        json.dumps({'boundary': 'engine.numbered_candidate', "
        "'plugin': str(kwargs.get('plugin'))}))\n"
        "    raise SystemExit(79)\n"
        "real.engine.numbered_candidate = construction_fence\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(real.main())\n",
        encoding="utf-8",
    )
    incumbent = home / ".copilot/installed-plugins" / marketplace / "agent-worktrees"
    published = root / "registered-payload"
    # Marketplace acquisition publishes the release form, not escaping Dev refs.
    _copy_fixture_tree(fresh, published, symlinks=True)
    release = _load(REPO / "tools/materialize_main.py", "engine_repair_release_materializer")
    materialized = release.materialize_uv_editable_ref_into(
        source_consumer_dir=fresh, dest_consumer_dir=published, canonical_root=source,
    )
    skipped = [row for row in materialized if row.startswith("SKIP ")]
    assert not skipped, "\n".join(skipped)
    _copy_fixture_tree(published, incumbent, symlinks=True)
    cli = tools / ("copilot.exe" if os.name == "nt" else "copilot")
    cli.write_text("synthetic acquisition executable; never launched\n", encoding="utf-8")
    cli.chmod(0o700)
    settings = home / ".copilot/settings.json"
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {
            marketplace: {"source": {"source": "github", "repo": "Example/Extensions"}},
        },
        "enabledPlugins": {f"agent-worktrees@{marketplace}": True},
    }), encoding="utf-8")
    ledger = home / ".copilot/config.json"
    ledger.write_text(json.dumps({"installedPlugins": [{
        "name": "agent-worktrees", "marketplace": marketplace,
        "version": json.loads((incumbent / "plugin.json").read_text())["version"],
        "cache_path": str(incumbent),
    }]}), encoding="utf-8")
    from worktree_manager import engine_origin

    acquisitions = []

    def acquire_registered_payload(command, environment, cwd, **kwargs):
        assert command[-3:] == ["plugin", "update", f"agent-worktrees@{marketplace}"]
        assert environment["COPILOT_HOME"] == str(home / ".copilot")
        acquisitions.append(list(command))
        _copy_fixture_tree(published, incumbent, symlinks=True, dirs_exist_ok=True)
        return subprocess.CompletedProcess(command, 0, "updated successfully, already at latest", "")

    monkeypatch.setattr(engine_origin, "_native_acquisition", acquire_registered_payload)
    binding = _load(fresh / "scripts/command_binding.py", "engine_repair_fresh_binding")
    primitive = binding._load_primitive(fresh)
    receipt = binding.publish_legacy_binding(
        primitive, payload_root=incumbent, plugin_id="agent-worktrees", legacy_root=runtime,
        environment=dict(os.environ),
    )
    manifest_path = runtime / "deploy-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 3, "service": "agent-worktrees", "commandBinding": receipt,
    }), encoding="utf-8")
    selected = binding.resolve_command_binding(
        primitive, payload_root=incumbent, plugin_id="agent-worktrees", legacy_root=runtime,
    )
    assert selected["status"] == "selected", selected
    assert selected["source"]["identity"]["canonical"].startswith("github:")
    checkout_identity = primitive.resolve_payload_source(
        payload_root=fresh, plugin_id="agent-worktrees",
    )
    assert checkout_identity["source"]["canonical"].startswith("directory:")
    assert receipt["runtimeRoot"] == str(runtime)
    from worktree_manager import update_bootstrap

    calls = []

    def execute_actual_installer(command, **options):
        assert not options.get("start_new_session")
        if os.name == "nt" and any(Path(part).name == "install.ps1" for part in command):
            if "-BootstrapPython" not in command:
                # Exercise this native interpreter, not an unrelated registered seed.
                command = [*command, "-BootstrapPython", str(Path(
                    getattr(sys, "_base_executable", sys.executable),
                ).resolve())]
        cwd = options.pop("cwd", home)
        # Scoped preflight performs the real repeated governance reads before
        # the fence; its measured native path needs headroom beyond shared admission.
        timeout = 45 if options.get("env", {}).get("COPILOT_EXTENSIONS_CONTEXT") else 30
        result = REAL_RUN(
            command, **options, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        calls.append((list(command), result))
        return result

    def observe_lifecycle(command, environment, cwd):
        return execute_actual_installer(
            command, env=environment, cwd=cwd, **no_window_kwargs(),
        ).returncode

    # Observe the actual canonical CLI/native dispatch, not a fake result.
    monkeypatch.setattr(update_bootstrap, "_wait", observe_lifecycle)

    @contextmanager
    def admitted_incumbent():
        from update_driver import participant_environment

        lifecycle = binding._load_lifecycle()
        with lifecycle.legacy_lifecycle(
            binding._rules(), primitive, payload_root=incumbent, plugin_id="agent-worktrees",
            legacy_root=runtime, project_root=home, environment=dict(os.environ),
            managed_deployment=True,
        ) as lease:
            # These are produced/verified by the actual supported lifecycle,
            # never a forged ticket or an authority-changing source descriptor.
            with monkeypatch.context() as child:
                child.setenv(lifecycle.TICKET_ENV, json.dumps(lease.ticket))
                child.setenv("COPILOT_PLUGIN_STAGED_FROM", str(incumbent))
                for key, value in participant_environment().items():
                    child.setenv(key, value)
                yield lease

    case = SimpleNamespace(
        home=home, runtime=runtime, source=source, fresh=fresh, incumbent=incumbent,
        effect=effect, binding=binding, primitive=primitive, receipt=receipt,
        manifest=manifest_path, update=update_bootstrap, calls=calls,
        admitted_incumbent=admitted_incumbent,
        execute=execute_actual_installer,
        acquisitions=acquisitions,
    )
    from update_driver import ParticipantError, RequestSpec, TrustedRunner, core, participant
    from update_driver.participant import ENVIRONMENT_KEY

    driver_root = home / ".copilot-extensions" / "update-driver"
    update_request = core.enqueue(
        RequestSpec("manager", repair=True),
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(home)),
        root=driver_root,
    )
    ready = queue.Queue()
    finished = threading.Event()

    def own_update(transaction):
        ready.put(transaction.participant_env())
        # Fixture teardown owns this wait; the contained runner bounds the tree.
        finished.wait()
        return 0

    owner = temporary.enter_context(ThreadPoolExecutor(max_workers=1))
    draining = owner.submit(core.drain, root=driver_root, executor=own_update)
    try:
        transport = ready.get(timeout=10)
        # Direct repair entry tests retain the real public updater's outer custody.
        with participant(root=driver_root, witness=transport[ENVIRONMENT_KEY]):
            yield case
    finally:
        finished.set()
        try:
            assert draining.result(timeout=10) == 0
        except ParticipantError as error:
            # The deliberate constructor abort must retain an honest fence,
            # never be relabeled as completed installation or proven cleanup.
            assert effect.is_file() and "unclosed participants" in str(error)
            outcome = core.status(update_request.id, root=driver_root)
            assert outcome.state == "interrupted" and outcome.exit_code is None
            assert outcome.settlement_kind is None and outcome.released_at is None
        else:
            assert core.status(update_request.id, root=driver_root).exit_code == 0
        uv_calls = root / "uv-called"
        assert not uv_calls.exists(), (
            "construction fence did not contain UV effects: " + uv_calls.read_text()
        )
        temporary.close()


def _diagnostic(case, rc: int) -> str:
    command, result = case.calls[-1]
    return f"repair rc={rc}; command={command!r}\n{result.stdout}\n{result.stderr}"


def _legacy_publication(case: SimpleNamespace) -> bytes:
    manifest = json.loads(case.manifest.read_text())
    manifest.pop("commandBinding")
    manifest["source"] = {"kind": "marketplace", "path": str(case.incumbent)}
    case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    return case.manifest.read_bytes()


@pytest.mark.parametrize("iteration", ["first", "next"])
def test_repair_fixture_owns_python_temporary_directory(repair_case, iteration):
    expected = repair_case.home.parent / "tmp"
    assert Path(tempfile.gettempdir()) == expected
    with tempfile.TemporaryDirectory(prefix=f"{iteration}-") as temporary:
        assert Path(temporary).parent == expected


def test_repair_fixture_installed_payload_has_complete_owned_driver_graph(repair_case):
    from worktree_manager import self_install

    engine = self_install._runtime_installer().engine
    roots = engine._package_roots(repair_case.incumbent)
    assert all(root.is_relative_to(repair_case.incumbent) for root in roots)
    assert {"agent-update-driver", "agent-procutil", "agent-single-instance-lease", "agent-zdd"} <= {
        engine._package_name(root) for root in roots
    }


def test_legacy_source_owner_capture_is_readonly_before_acquisition(
    repair_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import engine_origin

    case = repair_case
    before = _legacy_publication(case)
    acquire = engine_origin._native_acquisition

    def unchanged_owner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert case.manifest.read_bytes() == before
        return acquire(*args, **kwargs)

    monkeypatch.setattr(engine_origin, "_native_acquisition", unchanged_owner)
    refreshed = case.update._refresh_source(case.source, dict(os.environ))
    assert refreshed.identity["installationId"] == case.receipt["installationId"]
    assert case.manifest.read_bytes() == before
    assert len(case.acquisitions) == 1
    assert not case.calls and not case.effect.exists()


def test_legacy_source_owner_reaches_canonical_repair_construction(
    repair_case: SimpleNamespace,
) -> None:
    case = repair_case
    _legacy_publication(case)
    (case.runtime / "bin").mkdir()
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), _diagnostic(case, rc)
    assert len(case.acquisitions) == 1
    assert str(case.incumbent) in case.calls[-1][0]


@pytest.mark.parametrize("fault", ["missing-source", "relative", "unregistered", "malformed-binding"])
def test_legacy_source_capture_refuses_unsupported_evidence_before_acquisition(
    repair_case: SimpleNamespace, fault: str,
) -> None:
    case = repair_case
    _legacy_publication(case)
    manifest = json.loads(case.manifest.read_text())
    if fault == "missing-source":
        manifest.pop("source")
    elif fault == "relative":
        manifest["source"]["path"] = "relative/payload"
    elif fault == "unregistered":
        (case.home / ".copilot/settings.json").write_text("{}", encoding="utf-8")
    else:
        manifest["commandBinding"] = []
    case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert case.manifest.read_bytes() == before
    assert not case.calls and not case.acquisitions and not case.effect.exists()


def test_legacy_locator_owner_change_during_acquisition_refuses_effects(
    repair_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import engine_origin

    case = repair_case
    _legacy_publication(case)
    other = case.home / ".copilot/installed-plugins/other/agent-worktrees"
    shutil.copytree(case.incumbent, other)
    settings = case.home / ".copilot/settings.json"
    value = json.loads(settings.read_text())
    value["extraKnownMarketplaces"]["other"] = {
        "source": {"source": "github", "repo": "Other/Extensions"},
    }
    settings.write_text(json.dumps(value), encoding="utf-8")
    acquire = engine_origin._native_acquisition

    def change_owner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result = acquire(*args, **kwargs)
        manifest = json.loads(case.manifest.read_text())
        manifest["source"]["path"] = str(other)
        case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        return result

    monkeypatch.setattr(engine_origin, "_native_acquisition", change_owner)
    assert case.update.repair_engine(case.source) != 0
    assert len(case.acquisitions) == 1
    assert not case.calls and not case.effect.exists()


def test_repair_engine_marketplace_incumbent_reaches_canonical_construction(repair_case):
    case = repair_case
    (case.runtime / "bin").mkdir()
    before = case.manifest.read_bytes()
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), _diagnostic(case, rc)
    assert json.loads(case.effect.read_text())["boundary"] == "engine.numbered_candidate"
    assert json.loads(before)["commandBinding"]["installationId"] == case.receipt["installationId"]
    command = case.calls[-1][0]
    assert command[command.index("--source-payload-root") + 1] == str(case.incumbent)
    script = "scripts/install.ps1" if os.name == "nt" else "scripts/install.sh"
    assert str(case.incumbent / script) in command
    assert "update" in command
    assert "-ProvisionMissingRuntime" not in command
    assert "--provision-missing-runtime" not in command
    assert len(case.acquisitions) == 1


@pytest.mark.parametrize("damage", ["corrupt-installer", "missing-entire-payload"])
def test_repair_engine_rehydrates_source_before_executing_installer(repair_case, damage):
    case = repair_case
    if damage == "missing-entire-payload":
        case.incumbent.rename(case.incumbent.with_name("retained-source-backup"))
    else:
        name = "install.ps1" if os.name == "nt" else "install.sh"
        (case.incumbent / "scripts" / name).write_text("exit 97\n", encoding="utf-8")
    assert case.update.repair_engine(case.source) != 0
    assert len(case.acquisitions) == 1
    assert case.effect.is_file()


def test_repair_engine_missing_bin_selects_constructing_action_after_admission(repair_case):
    case = repair_case
    assert not (case.runtime / "bin").exists()
    with case.admitted_incumbent():
        assert not (case.runtime / "bin").exists()
        rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), _diagnostic(case, rc)
    command = case.calls[-1][0]
    assert "update" in command and "provision" not in command
    flag = "-ProvisionMissingRuntime" if os.name == "nt" else "--provision-missing-runtime"
    assert flag in command
    assert len(case.calls) == 1


def test_repair_engine_supported_lifecycle_install_control_reaches_boundary(repair_case):
    case = repair_case
    assert not (case.runtime / "bin").exists()
    suffix = "install.ps1" if os.name == "nt" else "install.sh"
    script = case.fresh / "scripts" / suffix
    if os.name == "nt":
        host = shutil.which("pwsh") or shutil.which("powershell.exe")
        command = [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "install"]
    else:
        command = [shutil.which("bash"), str(script), "install"]
    with case.admitted_incumbent():
        result = case.execute(
            command, env=dict(os.environ), **no_window_kwargs(),
        )
    assert case.effect.is_file(), _diagnostic(case, result.returncode)
    assert json.loads(case.effect.read_text())["boundary"] == "engine.numbered_candidate"
    assert result.returncode != 0, "fenced construction must not claim full repair success"


@pytest.mark.parametrize(
    "host_name", ["powershell.exe", "pwsh"] if os.name == "nt" else ["bash"],
)
def test_admitted_native_update_can_construct_a_missing_runtime(repair_case, host_name):
    case = repair_case
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    command = [host]
    if os.name == "nt":
        command += ["-NoProfile", "-NonInteractive", "-File"]
    suffix = "install.ps1" if os.name == "nt" else "install.sh"
    command += [
        str(case.fresh / "scripts" / suffix), "update",
        "-InstallDir" if os.name == "nt" else "--install-dir", str(case.runtime),
        "-ProvisionMissingRuntime" if os.name == "nt" else "--provision-missing-runtime",
    ]
    with case.admitted_incumbent():
        assert not (case.runtime / "bin").exists()
        result = case.execute(command, env=dict(os.environ), **no_window_kwargs())
    assert (case.runtime / "bin").is_dir()
    assert case.effect.is_file(), _diagnostic(case, result.returncode)
    assert result.returncode != 0, "the construction fence must still report failure"


def test_repair_engine_unsealed_origin_hint_does_not_bypass_ownership(repair_case, monkeypatch):
    case = repair_case
    forged = str(case.home / "foreign-unsealed-origin")
    monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", forged)
    rc = case.update.repair_engine(case.source)
    assert rc != 0
    assert case.effect.is_file(), _diagnostic(case, rc)
    command = case.calls[-1][0]
    assert command[command.index("--source-payload-root") + 1] == str(case.incumbent)
    assert forged not in command
    assert os.environ["COPILOT_PLUGIN_STAGED_FROM"] == forged


@pytest.mark.parametrize("mismatch", ["source", "installation", "environment"])
def test_repair_engine_refuses_foreign_incumbent_identity(repair_case, monkeypatch, mismatch):
    case = repair_case
    manifest = json.loads(case.manifest.read_text())
    if mismatch == "source":
        manifest["commandBinding"]["sourceFingerprint"] = "sha256:" + "0" * 64
    elif mismatch == "installation":
        manifest["commandBinding"]["runtimeRoot"] = str(case.home / "foreign-root")
    else:
        manifest["commandBinding"]["environment"]["homeRealPath"] = str(case.home / "foreign-home")
    case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", str(case.incumbent))
    selected = case.binding.resolve_command_binding(
        case.primitive, payload_root=case.incumbent, plugin_id="agent-worktrees",
        legacy_root=case.runtime,
    )
    assert selected["status"] == "blocked"
    before = case.manifest.read_bytes()
    rc = case.update.repair_engine(case.source)
    assert rc != 0
    assert not case.effect.exists()
    assert case.manifest.read_bytes() == before
    assert not case.calls
    assert not case.acquisitions


def test_repair_engine_refuses_unbound_context_even_with_source_hint(repair_case, monkeypatch):
    case = repair_case
    manifest = json.loads(case.manifest.read_text())
    manifest.pop("commandBinding")
    case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", str(case.incumbent))
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert not case.effect.exists()
    assert not case.calls
    assert case.manifest.read_bytes() == before


def test_repair_engine_never_relabels_arbitrary_checkout_as_incumbent(repair_case):
    case = repair_case
    script = case.fresh / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
    script.write_text("exit 97\n", encoding="utf-8")
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), _diagnostic(case, rc)
    assert str(script) not in case.calls[-1][0]
    assert case.calls[-1][0][case.calls[-1][0].index("--source-payload-root") + 1] == str(
        case.incumbent,
    )


def test_repair_engine_scoped_context_never_falls_back_to_shared_root(repair_case, monkeypatch):
    case = repair_case
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(case.home / "unbound/install.json"))
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert not case.effect.exists()
    assert not case.calls
    assert case.manifest.read_bytes() == before


def test_repair_engine_missing_bin_completes_one_update_lifecycle(repair_case, monkeypatch):
    case = repair_case
    observed = case.update._wait
    phases = []

    def successful_construction_effect(command, environment, cwd):
        case.effect.unlink(missing_ok=True)
        rc = observed(command, environment, cwd)
        assert rc != 0
        assert case.effect.is_file(), _diagnostic(case, rc)
        assert "update" in command and "provision" not in command
        phases.append("update")
        # Only the already-reached construction effect's result is simulated;
        # admission and the native action/dir-creation policy above were real.
        return 0

    monkeypatch.setattr(case.update, "_wait", successful_construction_effect)
    assert case.update.repair_engine(case.source) == 0
    assert phases == ["update"]
    assert (case.runtime / "bin").is_dir()
    assert json.loads(case.manifest.read_text())["commandBinding"] == case.receipt


@pytest.mark.parametrize("scenario", ["unadmitted", "forged", "wrong-action", "no-root", "context"])
def test_missing_bin_option_requires_existing_legacy_update_admission(repair_case, scenario):
    case = repair_case
    suffix = "install.ps1" if os.name == "nt" else "install.sh"
    host = (shutil.which("pwsh") or shutil.which("powershell.exe")) if os.name == "nt" else shutil.which("bash")
    command = [host]
    if os.name == "nt":
        command += ["-NoProfile", "-NonInteractive", "-File"]
    command += [str(case.fresh / "scripts" / suffix),
                "provision" if scenario == "wrong-action" else "update"]
    if scenario != "no-root":
        command += ["-InstallDir" if os.name == "nt" else "--install-dir", str(case.runtime)]
    command += ["-ProvisionMissingRuntime" if os.name == "nt" else "--provision-missing-runtime"]
    environment = dict(os.environ)
    if scenario != "unadmitted":
        environment["COPILOT_EXTENSIONS_COMMAND_LIFECYCLE"] = "{}"
        environment["COPILOT_PLUGIN_STAGED_FROM"] = str(case.incumbent)
    if scenario == "context":
        environment["COPILOT_EXTENSIONS_CONTEXT"] = str(case.home / "unbound/install.json")
    before = case.manifest.read_bytes()
    result = case.execute(command, env=environment, **no_window_kwargs())
    assert result.returncode != 0
    assert not (case.runtime / "bin").exists()
    assert not case.effect.exists()
    assert case.manifest.read_bytes() == before
    if scenario != "forged":
        assert "admitted legacy update" in result.stdout + result.stderr


def test_normal_update_does_not_provision_a_missing_bin_implicitly(repair_case):
    case = repair_case
    suffix = "install.ps1" if os.name == "nt" else "install.sh"
    host = (shutil.which("pwsh") or shutil.which("powershell.exe")) if os.name == "nt" else shutil.which("bash")
    command = [host]
    if os.name == "nt":
        command += ["-NoProfile", "-NonInteractive", "-File"]
    command += [str(case.fresh / "scripts" / suffix), "update",
                "-InstallDir" if os.name == "nt" else "--install-dir", str(case.runtime)]
    with case.admitted_incumbent():
        before = case.manifest.read_bytes()
        result = case.execute(command, env=dict(os.environ), **no_window_kwargs())
        assert case.manifest.read_bytes() == before
    assert result.returncode != 0
    assert "Not installed" in result.stdout + result.stderr
    assert not (case.runtime / "bin").exists()
    assert not case.effect.exists()


def test_repair_missing_bin_option_preserves_a_file_created_before_native_entry(
    repair_case, monkeypatch,
):
    case = repair_case
    observed = case.update._wait
    bin_path = case.runtime / "bin"

    def substitute_bin(command, environment, cwd):
        bin_path.write_bytes(b"retained ordinary file")
        return observed(command, environment, cwd)

    monkeypatch.setattr(case.update, "_wait", substitute_bin)
    assert case.update.repair_engine(case.source) != 0
    assert bin_path.read_bytes() == b"retained ordinary file"
    assert not case.effect.exists()


def test_repair_missing_bin_option_preserves_a_link_created_before_native_entry(
    repair_case, monkeypatch,
):
    case = repair_case
    observed = case.update._wait
    bin_path = case.runtime / "bin"
    target = case.home.parent / "outside-runtime"
    target.mkdir()
    sentinel = target / "preserved"
    sentinel.write_bytes(b"outside")

    def substitute_bin(command, environment, cwd):
        try:
            bin_path.symlink_to(target, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows symbolic-link privilege unavailable")
            raise
        return observed(command, environment, cwd)

    monkeypatch.setattr(case.update, "_wait", substitute_bin)
    assert case.update.repair_engine(case.source) != 0
    assert bin_path.is_symlink()
    assert sentinel.read_bytes() == b"outside"
    assert not case.effect.exists()


def test_missing_bin_repair_retains_its_git_prerequisite(repair_case, monkeypatch):
    case = repair_case
    tools = case.home.parent / "broken-git"
    tools.mkdir()
    git = tools / ("git.cmd" if os.name == "nt" else "git")
    git.write_text("@exit /b 17\r\n" if os.name == "nt" else "#!/bin/sh\nexit 17\n")
    git.chmod(0o700)
    observed = case.update._wait

    def failed_git(command, environment, cwd):
        child = {**environment, "PATH": str(tools) + os.pathsep + environment["PATH"]}
        return observed(command, child, cwd)

    monkeypatch.setattr(case.update, "_wait", failed_git)
    assert case.update.repair_engine(case.source) != 0
    assert not (case.runtime / "bin").exists()
    assert not case.effect.exists()
    assert "Missing prerequisite: git" in (
        case.calls[-1][1].stdout + case.calls[-1][1].stderr
    )


def test_repair_engine_invalid_bin_is_not_removed_or_reinstalled(repair_case):
    case = repair_case
    path = case.runtime / "bin"
    path.write_text("unrelated retained file", encoding="utf-8")
    assert case.update.repair_engine(case.source) != 0
    assert path.read_text() == "unrelated retained file"
    assert not case.calls
    assert not case.effect.exists()


def test_repair_engine_uses_management_policy_for_managed_incumbent(repair_case):
    case = repair_case
    arguments = {
        "payload_root": case.incumbent, "plugin_id": "agent-worktrees",
        "legacy_root": case.runtime, "environment": dict(os.environ),
    }
    decision = case.primitive.resolve_runtime_deployment(**arguments)
    (case.runtime / "runtime-deployment.json").write_text(json.dumps({
        "schema": case.primitive.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1, "managed": True,
        "owner": case.primitive._deployment_owner(decision, arguments),
    }), encoding="utf-8")
    assert case.primitive.resolve_runtime_deployment(**arguments)["deployment"][
        "allowPluginPublication"
    ] is False
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), _diagnostic(case, rc)
    assert "--managed-deployment" in case.calls[-1][0]


def test_repair_engine_revalidates_captured_owner_after_acquisition(repair_case, monkeypatch):
    from worktree_manager import engine_origin

    case = repair_case
    acquire = engine_origin._native_acquisition

    def change_owner(command, environment, cwd, **kwargs):
        result = acquire(command, environment, cwd, **kwargs)
        manifest = json.loads(case.manifest.read_text())
        manifest["commandBinding"]["runtimeRoot"] = str(case.home / "changed-root")
        case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        return result

    monkeypatch.setattr(engine_origin, "_native_acquisition", change_owner)
    assert case.update.repair_engine(case.source) != 0
    assert len(case.acquisitions) == 1
    assert not case.calls
    assert not case.effect.exists()
    assert not (case.runtime / "bin").exists()
