"""Dependency-body freshness and selected test-cache rebuild contracts."""
from pathlib import Path, PureWindowsPath
import os
import shutil
import subprocess
import tomllib
from types import SimpleNamespace

import pytest

from test_run_plugin_tests import runner
from plugin_test_cache import FLEET_TEST_CONSUMERS
import plugin_test_cache as cache_policy
import native_test_paths


def _project(root: Path, name: str, sources: str = "") -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0"\n'
        + (f"[tool.uv.sources]\n{sources}\n" if sources else ""),
        encoding="utf-8",
    )
    body = root / "module.py"
    body.write_text("VALUE = 1\n", encoding="utf-8")
    return body


@pytest.fixture
def graph(tmp_path, monkeypatch):
    plugin = tmp_path / "plugins" / "example"
    _project(plugin, "example", 'direct = {path = "../../libs/direct"}')
    direct = _project(
        tmp_path / "libs" / "direct", "direct", 'transitive = {path = "../transitive"}',
    )
    transitive = _project(tmp_path / "libs" / "transitive", "transitive")
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    return plugin, direct, transitive


@pytest.mark.parametrize("index", [1, 2])
@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_same_version_local_dependency_body_invalidates_cache(graph, index, change):
    before = runner._dep_fingerprint("example")
    body = graph[index]
    if change == "edit":
        body.write_text("VALUE = 2\n", encoding="utf-8")
    elif change == "add":
        body.with_name("added.py").write_text("NEW = True\n", encoding="utf-8")
    else:
        body.unlink()
    assert runner._dep_fingerprint("example") != before


def test_generated_and_unrelated_files_do_not_invalidate_cache(graph):
    before = runner._dep_fingerprint("example")
    dependency = graph[1].parent
    for directory in ("__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"):
        target = dependency / directory
        target.mkdir()
        (target / "generated.txt").write_text("ignored", encoding="utf-8")
    (dependency / "module.pyc").write_bytes(b"ignored")
    _project(dependency.parent / "unrelated", "unrelated")
    assert runner._dep_fingerprint("example") == before


def test_missing_transitive_source_refuses_instead_of_reusing_stamp(graph):
    (graph[2].parent / "pyproject.toml").unlink()
    with pytest.raises(FileNotFoundError):
        runner._dep_fingerprint("example")


def test_script_only_target_has_a_stable_runner_only_cache_key(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    before = runner._dep_fingerprint("script-only")
    (tmp_path / "script.py").write_text("VALUE = 1\n")
    assert runner._dep_fingerprint("script-only") == before
    monkeypatch.setattr(runner, "RUNNER_DEPENDENCIES", ("pytest-timeout>=2.4,<3",))
    assert runner._dep_fingerprint("script-only") != before


def test_payload_fixture_driver_transitive_body_invalidates_cache(graph, monkeypatch):
    repo = runner.REPO
    payload = repo / "libs" / "payload-invocation"
    payload.mkdir()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: payload)
    _project(
        repo / "libs" / "update-driver", "agent-update-driver",
        'direct = {path = "../direct"}',
    )
    before = runner._dep_fingerprint("payload-invocation")
    graph[2].write_text("VALUE = 2\n", encoding="utf-8")
    assert runner._dep_fingerprint("payload-invocation") != before


def test_manager_bridge_test_closure_tracks_transitive_source_without_a_vendor_pointer(graph, monkeypatch):
    repo = runner.REPO
    manager = repo / "worktree-manager"
    _project(manager, "copilot-extensions-worktree-manager")
    _project(
        repo / "plugins" / "agent-bridge", "agent-bridge",
        'direct = {path = "../../libs/direct"}',
    )
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: manager)
    before = runner._dep_fingerprint("worktree-manager")
    graph[2].write_text("VALUE = 2\n", encoding="utf-8")
    assert runner._dep_fingerprint("worktree-manager") != before
    assert "[tool.uv.sources]" not in (manager / "pyproject.toml").read_text()


def test_explicit_clean_does_not_prepare_a_busy_environment(graph, monkeypatch, capsys):
    root = runner.VENV_ROOT / "example"
    root.mkdir(parents=True)
    marker = root / "preserved"
    marker.write_text("keep")
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")

    def busy(*args):
        raise runner.AlreadyRunningError(root / "lock", 123)

    def unexpected(*args, **kwargs):
        pytest.fail("a busy clean must not prepare or remove the environment")

    monkeypatch.setattr(runner, "_acquire_admission", busy)
    monkeypatch.setattr(runner, "_ensure_venv", unexpected)
    assert runner.main(["example", "--reinstall", "--prepare-only"]) == 3
    assert "[BUSY]" in capsys.readouterr().err
    assert marker.read_text() == "keep"


@pytest.mark.parametrize("name", sorted(FLEET_TEST_CONSUMERS))
def test_retained_build_resource_body_invalidates_fleet_consumer(graph, name):
    repo = runner.REPO
    _project(repo / "fleet-manager", "fleet-manager")
    if name == "agent-dispatch":
        _project(repo / "plugins" / "agent-pull-requests", "agent-pull-requests")
    for resource in runner.standalone_build_resources("fleet-manager", source_root=repo):
        resource.mkdir(parents=True)
    body = repo / "libs" / "installer-engine" / "build.py"
    body.write_text("VALUE = 1\n")
    before = runner._dep_fingerprint(name)
    body.write_text("VALUE = 2\n")
    assert runner._dep_fingerprint(name) != before


@pytest.fixture
def released_graph(tmp_path, monkeypatch):
    plugin = tmp_path / "plugins" / "agent-pull-requests"
    fleet = tmp_path / "fleet-manager"
    for project, name in ((plugin, "agent-pull-requests"), (fleet, "fleet-manager")):
        _project(project, name, 'shared = {path = "libs/shared"}')
        _project(project / "libs" / "shared", "shared")
        manifest = project / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace(
            'version = "1.0"',
            'version = "1.0"\ndependencies = ["shared>=1", '
            '"shared>=999; sys_platform == \'inactive-test-platform\'"]',
        ) + '\n[project.optional-dependencies]\nunused = ["shared>=500"]\n')
    canonical = _project(tmp_path / "libs" / "shared", "shared")
    for resource in runner.standalone_build_resources("fleet-manager", source_root=tmp_path):
        resource.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    return plugin, fleet, canonical


def test_released_library_overrides_are_canonical_and_invalidate_cache(released_graph):
    plugin, _, canonical = released_graph
    before = runner._dep_fingerprint("agent-pull-requests")
    overrides, _ = cache_policy.canonical_test_sources(
        "agent-pull-requests", plugin, runner.REPO,
    )
    assert overrides == {"shared": canonical.parent}
    canonical.write_text("VALUE = 2\n")
    assert runner._dep_fingerprint("agent-pull-requests") != before
    (canonical.parent / "pyproject.toml").unlink()
    with pytest.raises(ValueError, match="conflicting non-library sources"):
        runner._dep_fingerprint("agent-pull-requests")


@pytest.fixture
def preparation_metadata(tmp_path, monkeypatch):
    import installer_dependency_policy
    from agent_procutil import owned_process
    monkeypatch.setenv("UV_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    monkeypatch.setattr(installer_dependency_policy, "_select_uv_executable", lambda uv: "fixture-uv")
    monkeypatch.setattr(
        owned_process, "run_caller_owned",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, (str(tmp_path / "credentials") + "\n").encode(), b"",
        ),
    )


def test_released_preparation_passes_overrides_through_normal_policy(released_graph, monkeypatch, preparation_metadata):
    plugin, _, canonical = released_graph
    (runner.VENV_ROOT / "agent-pull-requests").mkdir(parents=True)
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )
    calls = []

    def install(uv, arguments, **options):
        source = Path(arguments[arguments.index("--override") + 1])
        constraints = Path(arguments[arguments.index("--constraint") + 1])
        assert "shared>=1\n" in constraints.read_text()
        assert "shared>=500" not in constraints.read_text()
        calls.append((source.read_text(), options))

    monkeypatch.setattr(runner, "run_uv_install", install)
    runner._ensure_venv("agent-pull-requests", "fixture-uv", reinstall=False)
    text, options = calls.pop()
    assert text == f"shared @ {canonical.parent.as_uri()}\n"
    assert options["cwd"] == plugin and options["trusted_source_roots"] == (runner.REPO,)
    assert not calls


def test_released_constraints_follow_selected_library_extras(released_graph):
    plugin, _, canonical = released_graph
    metadata = plugin / "pyproject.toml"
    metadata.write_text(metadata.read_text().replace("shared>=1", "shared[feature]>=1"))
    library = canonical.parent / "pyproject.toml"
    library.write_text(library.read_text() + (
        '\n[project.optional-dependencies]\nfeature = ["shared>=2"]\n'
        'unused = ["shared>=500"]\n'
    ))
    _, constraints = cache_policy.canonical_test_sources("agent-pull-requests", plugin, runner.REPO)
    assert "shared>=2" in constraints
    assert not any("shared>=500" in requirement for requirement in constraints)


@pytest.mark.parametrize("graph", ["original", "canonical"])
@pytest.mark.parametrize("selector", [
    "marker = \"sys_platform == 'win32'\"", 'extra = "feature"', 'group = "dev"',
])
def test_released_composition_refuses_source_activation_selectors(released_graph, graph, selector):
    plugin, _, canonical = released_graph
    metadata = (
        plugin / "pyproject.toml" if graph == "original" else canonical.parent / "pyproject.toml"
    )
    if graph == "original":
        metadata.write_text(metadata.read_text().replace(
            'path = "libs/shared"', f'path = "libs/shared", {selector}',
        ))
    else:
        metadata.write_text(metadata.read_text() + (
            f'\n[tool.uv.sources]\nshared = {{path = ".", {selector}}}\n'
        ))
    with pytest.raises(ValueError, match="unconditional source mappings"):
        cache_policy.source_resolution_arguments(
            "agent-pull-requests", plugin, runner.REPO, runner.REPO / "uncreated-venv",
        )
    assert not (runner.REPO / "uncreated-venv").exists()


def _copy_metadata_checkout(source, checkout, consumer):
    engine = source / "libs" / "installer-engine"
    for source_file in engine.iterdir():
        if source_file.is_file():
            destination = checkout / "libs" / "installer-engine" / source_file.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination)
    roots = cache_policy._dependency_sources(consumer, source / "plugins" / consumer, source)
    # Materialization reads every declared local source, including requirements
    # inactive on the current platform; dependency resolution deliberately does not.
    pending = list(roots)
    while pending:
        manifest = pending.pop() / "pyproject.toml"
        if not manifest.is_file():
            continue
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        for selected in data.get("tool", {}).get("uv", {}).get("sources", {}).values():
            for entry in selected if isinstance(selected, list) else [selected]:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    continue
                dependency = (manifest.parent / entry["path"]).resolve()
                if dependency.is_relative_to(source.resolve()) and dependency not in roots:
                    roots.add(dependency)
                    pending.append(dependency)
    names = set()
    for root in roots:
        manifest = root / "pyproject.toml"
        if manifest.is_file():
            name = tomllib.loads(manifest.read_text(encoding="utf-8")).get("project", {}).get("name")
            if isinstance(name, str):
                names.add(name)
    for manifest in (source / "libs").glob("*/pyproject.toml"):
        name = tomllib.loads(manifest.read_text(encoding="utf-8")).get("project", {}).get("name")
        if name in names:
            roots.update(cache_policy._package_roots(manifest.parent))
    for root in roots:
        target = checkout / root.relative_to(source)
        target.mkdir(parents=True, exist_ok=True)
        if (root / "pyproject.toml").is_file():
            shutil.copyfile(root / "pyproject.toml", target / "pyproject.toml")
            (target / "src").mkdir(exist_ok=True)
            (target / "src" / "fixture.py").write_text("# Metadata-only release fixture.\n")


@pytest.mark.parametrize("consumer", ["agent-pull-requests", "agent-dispatch"])
@pytest.mark.parametrize("source_layout", ["checkout", "materialized"])
def test_real_materialized_metadata_preserves_active_platform_constraints(
    tmp_path, consumer, source_layout,
):
    import materialize_main

    source = Path(__file__).resolve().parents[1]
    if source_layout == "materialized":
        snapshot = tmp_path / "input"
        _copy_metadata_checkout(source, snapshot, consumer)
        log = materialize_main.materialize_uv_editable_refs(snapshot, canonical_root=snapshot)
        assert not any(line.startswith("SKIP") for line in log), [line for line in log if line.startswith("SKIP")]
        source = snapshot
    checkout = tmp_path / "release"
    _copy_metadata_checkout(source, checkout, consumer)
    log = materialize_main.materialize_uv_editable_refs(checkout, canonical_root=checkout)
    assert not any(line.startswith("SKIP") for line in log), [line for line in log if line.startswith("SKIP")]
    overrides, constraints = cache_policy.canonical_test_sources(
        consumer, checkout / "plugins" / consumer, checkout,
    )
    assert "agent-zdd" in overrides
    assert any(
        requirement.startswith("agent-zdd;") and "sys_platform" in requirement
        for requirement in constraints
    )


@pytest.mark.parametrize("consumer", ["agent-pull-requests", "agent-dispatch"])
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_real_uv_resolves_released_duplicate_sources_with_canonical_override(
    released_graph, consumer,
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("actual UV resolver is unavailable")
    plugin, fleet, canonical = released_graph
    projects = [plugin, fleet]
    if consumer == "agent-dispatch":
        plugin = runner.REPO / "plugins" / consumer
        _project(plugin, consumer, 'shared = {path = "libs/shared"}')
        _project(plugin / "libs" / "shared", "shared")
        metadata = plugin / "pyproject.toml"
        metadata.write_text(metadata.read_text().replace(
            'version = "1.0"', 'version = "1.0"\ndependencies = ["shared>=1"]',
        ))
        projects.insert(0, plugin)
    metadata = plugin / "pyproject.toml"
    metadata.write_text(metadata.read_text().replace("shared>=1", "shared[feature_name]>=1"))
    library = canonical.parent / "pyproject.toml"
    library.write_text(library.read_text().replace(
        'version = "1.0"',
        'version = "1.0"\ndependencies = ["shared>=1; extra == \'FEATURE_NAME\'"]',
    ) + '\n[project.optional-dependencies]\nFEATURE_NAME = []\n')
    requirements = runner.REPO / "requirements.txt"
    requirements.write_text("".join(f"-e {project}\n" for project in projects))
    command = [
        uv, "pip", "compile", "--no-config", "--offline", "--no-build",
        str(requirements),
    ]
    options = dict(capture_output=True, text=True, timeout=30)
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    refused = subprocess.run(command, **options)
    assert refused.returncode != 0 and "conflicting URLs" in refused.stderr
    venv = runner.REPO / "venv"
    venv.mkdir()

    def resolve():
        arguments = cache_policy.source_resolution_arguments(
            consumer, plugin, runner.REPO, venv,
        )
        return subprocess.run([*command, *arguments], **options)

    accepted = resolve()
    assert accepted.returncode == 0, accepted.stderr
    assert canonical.parent.as_uri() in accepted.stdout
    original_library = library.read_text()
    library.write_text(original_library.replace("shared>=1;", "shared>=2;"))
    active_extra = resolve()
    assert active_extra.returncode != 0, active_extra.stdout
    library.write_text(original_library)
    metadata.write_text(metadata.read_text().replace("shared[feature_name]>=1", "shared[feature_name]>=2"))
    incompatible = resolve()
    assert incompatible.returncode != 0, incompatible.stdout


@pytest.mark.parametrize("linked_parent", [False, True])
def test_linked_cache_root_refuses_without_touching_target(graph, monkeypatch, linked_parent, preparation_metadata):
    target = runner.VENV_ROOT / "real"
    target.mkdir(parents=True)
    marker = target / "preserved"
    marker.write_text("keep")
    alias = runner.VENV_ROOT / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")
    monkeypatch.setattr(runner, "VENV_ROOT", alias if linked_parent else runner.VENV_ROOT)
    with pytest.raises(ValueError, match="not ordinary"):
        runner._ensure_venv("example" if linked_parent else "alias", "unused", reinstall=True)
    assert marker.read_text() == "keep"


def test_automatic_body_drift_rebuild_refreshes_uv_and_preserves_other_cache(
    graph, monkeypatch, preparation_metadata,
):
    root = runner.VENV_ROOT / "example"
    root.mkdir(parents=True)
    python = root / "fixture-python"
    python.touch()
    (root / ".dep-fingerprint").write_text(runner._dep_fingerprint("example"))
    stale = root / "stale-package"
    stale.mkdir()
    (stale / "old.py").write_text("old")
    other = runner.VENV_ROOT / "unrelated"
    other.mkdir()
    marker = other / "preserved"
    marker.write_text("keep")
    identity = (root.stat().st_dev, root.stat().st_ino)
    graph[2].write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(runner, "_venv_python", lambda _name: python)
    calls = []

    def run(command, **kwargs):
        assert command[:3] == ["uv", "venv", "--allow-existing"]
        assert not stale.exists()
        python.touch()
        return subprocess.CompletedProcess(command, 0)

    def install(uv, arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess([uv, *arguments], 0)

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "run_uv_install", install)
    assert runner._ensure_venv("example", "uv", reinstall=False) == python
    assert len(calls) == 1
    assert calls[0].count("--reinstall") == 1
    assert (root.stat().st_dev, root.stat().st_ino) == identity
    assert marker.read_text() == "keep"
    assert (root / ".dep-fingerprint").read_text() == runner._dep_fingerprint("example")


def _overlong_private_wheel(root):
    return root / ".bootstrap-uv-cache" / ("source-" + "s" * 96) / ("build-" + "b" * 96) / (
        "example_distribution_with_long_metadata-1.0.0-py3-none-any.whl"
    )


def _native_private_paths(root):
    return (
        native_test_paths.native_test_path(root / ".bootstrap-uv-cache"),
        native_test_paths.native_test_path(_overlong_private_wheel(root)),
    )


@pytest.mark.parametrize("root", [
    r"\\server\share\cache", r"\\?\C:\cache", r"\\?\UNC\server\share\cache",
])
def test_private_fixture_preserves_unc_and_native_root_spelling(monkeypatch, root):
    monkeypatch.setattr(native_test_paths, "os", SimpleNamespace(name="nt"))
    logical = PureWindowsPath(root)
    cache, wheel = _native_private_paths(logical)
    assert str(cache) == str(native_test_paths.native_test_path(logical / ".bootstrap-uv-cache"))
    assert str(wheel) == str(native_test_paths.native_test_path(_overlong_private_wheel(logical)))


@pytest.mark.parametrize("root", ["C:\\", r"C:\t"])
def test_private_wheel_fixture_exceeds_max_path_with_minimal_root(root):
    wheel = _overlong_private_wheel(PureWindowsPath(root))
    assert len(str(wheel)) > 260
    assert all(len(part) <= 255 for part in wheel.parts[1:])


@pytest.mark.skipif(os.name != "nt", reason="Native Windows long-path cleanup")
def test_rebuild_removes_overlong_private_cache_without_replacing_leased_root(
    graph, monkeypatch,
):
    root = runner.VENV_ROOT / "example"
    root.mkdir(parents=True)
    python = root / "fixture-python"
    python.touch()
    (root / ".dep-fingerprint").write_text("stale")
    wheel = _overlong_private_wheel(root)
    assert len(str(wheel.absolute())) > 260
    native_cache, native_wheel = _native_private_paths(root)
    native_wheel.parent.mkdir(parents=True)
    native_wheel.write_bytes(b"private cached wheel")
    sibling = root.with_name("unrelated")
    sibling.mkdir()
    marker = sibling / "preserved"
    marker.write_text("keep")
    identity = (root.stat().st_dev, root.stat().st_ino)
    monkeypatch.setattr(runner, "_venv_python", lambda _name: python)
    calls = []

    def create(command, **kwargs):
        assert command[:3] == ["uv", "venv", "--allow-existing"]
        assert not native_cache.exists()
        assert (root.stat().st_dev, root.stat().st_ino) == identity
        python.touch()
        return subprocess.CompletedProcess(command, 0)

    def install(uv, arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess([uv, *arguments], 0)

    monkeypatch.setattr(runner.subprocess, "run", create)
    monkeypatch.setattr(runner, "run_uv_install", install)
    try:
        assert runner._ensure_venv("example", "uv", reinstall=False) == python
        assert len(calls) == 1 and "--reinstall" in calls[0]
        assert marker.read_text() == "keep"
        assert (root / ".dep-fingerprint").read_text() == runner._dep_fingerprint("example")
    finally:
        if native_cache.exists():
            shutil.rmtree(native_cache)


@pytest.mark.parametrize(("path", "expected"), [
    (r"C:\cache\example", r"\\?\C:\cache\example"),
    (r"\\server\share\cache", r"\\?\UNC\server\share\cache"),
    (r"\\?\C:\cache\example", r"\\?\C:\cache\example"),
])
def test_windows_cache_spelling_preserves_drive_unc_and_existing_prefix(
    monkeypatch, path, expected,
):
    monkeypatch.setattr(native_test_paths, "os", SimpleNamespace(name="nt"))
    assert str(native_test_paths.native_test_path(PureWindowsPath(path))) == expected


def test_non_windows_cache_spelling_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(native_test_paths, "os", SimpleNamespace(name="posix"))
    assert native_test_paths.native_test_path(tmp_path) is tmp_path


def test_cleanup_error_refuses_before_uv_or_ready_fingerprint(graph, monkeypatch, preparation_metadata):
    root = runner.VENV_ROOT / "example"
    root.mkdir(parents=True)
    python = root / "fixture-python"
    python.touch()
    (root / ".dep-fingerprint").write_text("stale")
    held = root / "held-cache"
    held.mkdir()
    marker = held / "preserved"
    marker.write_text("keep")
    identity = (root.stat().st_dev, root.stat().st_ino)
    monkeypatch.setattr(runner, "_venv_python", lambda _name: python)

    real_rmtree = cache_policy.shutil.rmtree
    def refuse(path, *args, **kwargs):
        if Path(path).name == held.name:
            raise PermissionError("owned cache is still held")
        return real_rmtree(path, *args, **kwargs)

    def unexpected(*args, **kwargs):
        pytest.fail("failed cleanup must not prepare or mark an environment ready")

    monkeypatch.setattr(cache_policy.shutil, "rmtree", refuse)
    monkeypatch.setattr(runner.subprocess, "run", unexpected)
    monkeypatch.setattr(runner, "run_uv_install", unexpected)
    with pytest.raises(PermissionError, match="still held"):
        runner._ensure_venv("example", "uv", reinstall=False)
    assert marker.read_text() == "keep"
    assert (root.stat().st_dev, root.stat().st_ino) == identity
    stamp = root / ".dep-fingerprint"
    assert not stamp.exists() or stamp.read_text() == "stale"


@pytest.mark.parametrize("location", ["root", "ancestor", "child"])
def test_cache_cleanup_retains_link_boundaries(tmp_path, location):
    root = tmp_path / "cache"
    root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    marker = target / "preserved"
    marker.write_text("keep")
    alias = (root / "alias") if location == "child" else tmp_path / "alias"
    if os.name == "nt":
        subprocess.run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(alias), str(target)],
            check=True, capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        alias.symlink_to(target, target_is_directory=True)
    if location == "child":
        identity = (root.stat().st_dev, root.stat().st_ino)
        cache_policy.clear_cache_contents(root)
        assert not alias.exists()
        assert (root.stat().st_dev, root.stat().st_ino) == identity
    else:
        candidate = alias
        if location == "ancestor":
            (target / "nested").mkdir()
            candidate /= "nested"
        with pytest.raises(ValueError, match="not ordinary"):
            cache_policy.clear_cache_contents(candidate)
    assert marker.read_text() == "keep"
