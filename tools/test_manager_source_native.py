"""Optional real Manager adapter/constructor contract with a synthetic closure."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, replace

import pytest
import runtime_feed_configuration as configuration
import runtime_source_build as builds
from test_installer_wheel_acquisition import CERTIFICATE, feeds as feeds  # noqa: F401
from test_runtime_feed_configuration import environment
from test_runtime_source_authentication import configured_source as configured_source  # noqa: F401
from test_runtime_source_authentication import adopter as adopter, git  # noqa: F401
from test_runtime_source_build import options, packaging_feed, select_source, tmp_path as tmp_path  # noqa: F401
from test_runtime_source_lock import plan, target as target  # noqa: F401

_ROOT = Path(__file__).resolve().parents[1]
for _path in ("worktree-manager/src", "worktree-manager/tests"):
    sys.path.insert(0, str(_ROOT / _path))

from test_manager_engine_adapter import _attribute_source, _closed_inputs, _source
from worktree_manager import self_install as si


def manager_source(adopter, configured_source, monkeypatch):
    root, repository, _, _ = configured_source
    installation = adopter[1]
    installation.update(plugin_id="worktree-manager", legacy_root=root)
    payload = installation["payload_root"].with_name("worktree-manager")
    payload.mkdir()
    installation["payload_root"] = payload
    settings = json.loads(adopter[2].read_text())
    settings["enabledPlugins"] = {key.replace("agent-example@", "worktree-manager@"): value
                                  for key, value in settings["enabledPlugins"].items()}
    adopter[2].write_text(json.dumps(settings))
    project, _, _ = _source(repository, version="1.2.3.dev1")
    project.rename(repository / "worktree-manager")
    project = repository / "worktree-manager"
    procutil = project / "libs" / "agent-procutil"
    shutil.copytree(_ROOT / "libs" / "agent-procutil" / "src", procutil / "src")
    backend = Path(__file__).parent / "fixtures" / "source-runtime-backend.py"
    for package in (project, procutil):
        with (package / "pyproject.toml").open("a", encoding="utf-8") if package == project else (
            package / "pyproject.toml"
        ).open("w", encoding="utf-8") as stream:
            if package == project:
                stream.write('dependencies=["agent-procutil"]\n[tool.uv.sources]\nagent-procutil={path="libs/agent-procutil"}\n')
            else:
                stream.write('[project]\nname="agent-procutil"\nversion="1.0.0"\ndependencies=[]\n')
            stream.write('[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n')
        shutil.copyfile(backend, package / "fixture_backend.py")
    (project / "src" / "worktree_manager" / "runtime_candidate.py").write_text(
        "import importlib.metadata, pathlib, shutil, sys\n"
        "def prepare_runtime_candidate(data):\n"
        "    shutil.copytree(data['frozen_plugin'], pathlib.Path(sys.prefix) / 'payload')\n"
        "def verify_runtime_candidate():\n"
        "    import worktree_manager\n"
        "    assert pathlib.Path(worktree_manager.__file__).is_relative_to(pathlib.Path(sys.prefix))\n"
        "    assert importlib.metadata.version('copilot-extensions-worktree-manager') == '1.2.3.dev1'\n",
        encoding="utf-8",
    )
    scripts = project / "scripts"
    scripts.mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py", "versioned_runtime_publication.py",
    ):
        shutil.copyfile(Path(si._runtime_installer().__file__).with_name(name), scripts / name)
    references = project / "references"
    references.mkdir()
    shutil.copyfile(
        _ROOT / "worktree-manager" / "references" / "control-plane-provider.json",
        references / "control-plane-provider.json",
    )
    monkeypatch.setattr(si, "local_bin", lambda: root / "bin")
    monkeypatch.setattr(si, "control_plane_providers_dir", lambda: root / "providers")
    select_source(configured_source)
    return root


@pytest.mark.parametrize("uv_selection", ["absolute", "command"])
def test_manager_source_warm_install_in_a_new_process_never_resolves_or_rebuilds(
    tmp_path, monkeypatch, adopter, configured_source, target, feeds, uv_selection,
):
    import hashlib

    root = manager_source(adopter, configured_source, monkeypatch)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path, authenticate="auto")
    env.update(UV_INDEX_GOVERNED_USERNAME="fixture-user", UV_INDEX_GOVERNED_PASSWORD="synthetic-secret")
    arguments = options(adopter, target, env)

    def install():
        return builds.install_manager_source_runtime(
            plan(adopter), **{**arguments, "deadline": time.monotonic() + 120},
        )

    if uv_selection == "absolute":
        path = os.pathsep.join(
            entry for entry in os.environ.get("PATH", "").split(os.pathsep)
            if not any((Path(entry) / name).is_file() for name in ("uv", "uv.exe", "uv.cmd"))
        )
        monkeypatch.setenv("PATH", path)
        env["PATH"] = path
        assert shutil.which("uv") is None
    else:
        arguments["uv"] = "uv"
        assert shutil.which("uv") is not None
    try:
        first = install()
    except subprocess.CalledProcessError as error:
        pytest.fail(error.stderr or str(error))
    assert first.action == "installed", first.reason
    slot = Path(first.slot)
    marker = builds.engine.runtime.marker_path(root, slot.name)
    before = marker.read_bytes(), marker.stat().st_mtime_ns
    receipt = (slot / ".runtime-input-origins.json").read_bytes()
    requests = len(state["requests"])
    assert requests
    state["routes"].clear()
    specification = {
        "installation": adopter[1], "target": target, "component": arguments["component"],
        "uv": arguments["uv"], "python": str(arguments["python"]), "env": env,
        "acquisition_limits": asdict(arguments["acquisition_limits"]),
        "wheel_limits": asdict(arguments["wheel_limits"]),
        "max_lock_bytes": arguments["max_lock_bytes"], "max_snapshot_bytes": arguments["max_snapshot_bytes"],
    }
    script = """
import hashlib, json, sys, time
import shutil
from dataclasses import asdict
from pathlib import Path
source = Path(sys.argv[1])
sys.path.insert(0, str(source / "tools"))
import runtime_source_build as builds
import runtime_feed_configuration as configuration
from runtime_profile_policy import plan_installation_profile
from worktree_manager import self_install as si
options = json.loads(sys.argv[2])
assert (shutil.which("uv") is None) == (options["uv"] != "uv")
installation = options["installation"]
for key in ("payload_root", "os_profile", "legacy_root", "copilot_home", "project_root"):
    if key in installation:
        installation[key] = Path(installation[key])
root = installation["legacy_root"]
si.local_bin = lambda: root / "bin"
si.control_plane_providers_dir = lambda: root / "providers"
configuration._opaque_index_identity = lambda value: hashlib.sha256(value.encode()).hexdigest()
def forbidden(*args, **kwargs):
    raise AssertionError("new-process warm installation attempted resolution or construction")
builds.retain_source_runtime_inputs = forbidden
builds.engine._construct = forbidden
options["python"] = Path(options["python"])
options["acquisition_limits"] = builds.AcquisitionLimits(**options["acquisition_limits"])
options["wheel_limits"] = builds.WheelLimits(**options["wheel_limits"])
options["deadline"] = time.monotonic() + 60
plan = plan_installation_profile(
    installation=installation, release_index=None, repository="example/plugins",
    signer_digest="", source_digest="", source_ref="", release={}, artifact=None,
    target={}, component="fixture", roots=[], max_subject_bytes=1,
)
result = builds.install_manager_source_runtime(plan, **options)
assert result.action == "already-current", result
print(json.dumps(asdict(result)))
"""
    second = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(_ROOT), json.dumps(specification, default=str)],
        capture_output=True, text=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert json.loads(second.stdout)["slot"] == first.slot
    assert len(state["requests"]) == requests
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
    assert (slot / ".runtime-input-origins.json").read_bytes() == receipt
    assert builds.engine.runtime.current_version(root) == slot.name
    assert builds.engine.runtime.read_last_known_good(root) == slot.name
    assert not git(configured_source[1], "status", "--porcelain=v1", "--ignored")
    proof = slot / ".runtime-input-origins.json"
    corrupted = json.loads(receipt)
    name = next(iter(corrupted["packages"]))
    corrupted["packages"][name] = "file:///altered/" + corrupted["packages"][name].rsplit("/", 1)[-1]
    site = slot / ("Lib/site-packages" if os.name == "nt" else (
        f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    ))
    origin = next(site.glob("*.dist-info/direct_url.json"))
    installed_origin = origin.read_bytes()
    changed_digest = json.loads(installed_origin)
    changed_digest["archive_info"] = {"hashes": {"sha256": "0" * 64}}
    with monkeypatch.context() as integrity:
        integrity.setattr(builds, "retain_source_runtime_inputs", lambda *args, **kwargs: pytest.fail(
            "corrupt input evidence reached cold source admission",
        ))
        for path, content in (
            (proof, json.dumps(corrupted)), (origin, json.dumps(changed_digest)), (origin, "{"),
        ):
            original = path.read_bytes()
            path.write_text(content)
            try:
                with pytest.raises(builds.dependencies.DependencyPolicyError, match="input evidence"):
                    install()
                assert builds.engine.runtime.current_version(root) == slot.name
                assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
                assert len(state["requests"]) == requests
            finally:
                path.write_bytes(original)
    installed_module = site / "worktree_manager" / "__init__.py"
    original_module = installed_module.read_bytes()
    with monkeypatch.context() as health:
        def needs_source(*args, **kwargs):
            raise RuntimeError("genuine installed health needs source admission")

        health.setattr(builds, "retain_source_runtime_inputs", needs_source)
        installed_module.write_text("raise RuntimeError('synthetic installed health failure')")
        try:
            with pytest.raises(RuntimeError, match="genuine installed health needs source admission"):
                install()
        finally:
            installed_module.write_bytes(original_module)
    assert builds.engine.runtime.current_version(root) == slot.name
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
    assert len(state["requests"]) == requests
    assert proof.read_bytes() == receipt and origin.read_bytes() == installed_origin
    with monkeypatch.context() as authentication:
        def changed_account(*args, **kwargs):
            raise RuntimeError("changed authenticated account requires ordinary source admission")

        authentication.setattr(builds, "retain_source_runtime_inputs", changed_account)
        env["UV_INDEX_GOVERNED_USERNAME"] = "other-user"
        try:
            with pytest.raises(RuntimeError, match="changed authenticated account"):
                install()
        finally:
            env["UV_INDEX_GOVERNED_USERNAME"] = "fixture-user"
    assert builds.engine.runtime.current_version(root) == slot.name
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
    assert len(state["requests"]) == requests
    environment(adopter, state, tmp_path, authenticate="auto", resolution="lowest")

    def requires_source(*args, **kwargs):
        raise RuntimeError("changed resolver mode requires ordinary source admission")

    monkeypatch.setattr(builds, "retain_source_runtime_inputs", requires_source)
    with pytest.raises(RuntimeError, match="changed resolver mode"):
        install()
    assert builds.engine.runtime.current_version(root) == slot.name
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
    marker.write_text("{")
    from runtime_profile import ProfileError
    with pytest.raises(ProfileError, match="completion evidence"):
        install()
    assert builds.engine.runtime.current_version(root) == slot.name
    assert marker.read_text() == "{"
    assert len(state["requests"]) == requests


def test_manager_closed_native(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, _ = _source(tmp_path, version="1.2.3.dev1")
    canonical = _ROOT / "libs" / "agent-procutil"
    procutil = payload / "libs" / "agent-procutil"
    shutil.copytree(canonical / "src", procutil / "src")
    (procutil / "pyproject.toml").write_text(
        '[project]\nname="agent-procutil"\nversion="1.0.0"\ndependencies=[]\n',
        encoding="utf-8",
    )
    with (payload / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            'dependencies=["agent-procutil"]\n'
            '[tool.uv.sources]\nagent-procutil={path="libs/agent-procutil"}\n',
        )
    uv = shutil.which("uv")
    assert uv is not None, "native adapter qualification requires UV"
    module = payload / "src" / "worktree_manager"
    (module / "runtime_candidate.py").write_text(
        "import importlib.metadata, pathlib, shutil, sys\n"
        "def prepare_runtime_candidate(data):\n"
        "    shutil.copytree(data['frozen_plugin'], pathlib.Path(sys.prefix) / 'payload')\n"
        "def verify_runtime_candidate():\n"
        "    import worktree_manager\n"
        "    assert pathlib.Path(worktree_manager.__file__).is_relative_to(pathlib.Path(sys.prefix))\n"
        "    assert importlib.metadata.version('copilot-extensions-worktree-manager') == '1.2.3.dev1'\n",
        encoding="utf-8",
    )
    scripts = payload / "scripts"
    scripts.mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(Path(adapter.__file__).with_name(name), scripts / name)
    _attribute_source(adapter, payload, version="1.2.3.dev1")
    fixture = Path(__file__).parent / "fixtures" / "source-runtime-backend.py"
    spec = importlib.util.spec_from_file_location("manager_fixture_backend", fixture)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    with monkeypatch.context() as building:
        building.chdir(payload)
        filename = backend.build_wheel(str(tmp_path))
        building.chdir(procutil)
        procutil_filename = backend.build_wheel(str(tmp_path))
    procutil_version = tomllib.loads((procutil / "pyproject.toml").read_text())["project"]["version"]
    content = (tmp_path / filename).read_bytes()
    monkeypatch.setenv("UV_INDEX_URL", "https://127.0.0.1:1/forbidden")
    monkeypatch.setenv("UV_CONFIG_FILE", str(tmp_path / "missing-config"))
    monkeypatch.setenv("UV_OFFLINE", "true")
    extra = (("agent-procutil", procutil_version, procutil_filename,
              (tmp_path / procutil_filename).read_bytes()),)
    with _closed_inputs(tmp_path, content, extra) as wheels:
        options = dict(
            assemble=si._copy_payload, uv=Path(uv), wheel_inputs=wheels,
            local_packages=("agent-procutil", "copilot-extensions-worktree-manager"),
        )
        with adapter.candidate(payload, root, **options) as ready:
            assert ready.reused is False
            slot = ready.slot
            receipt = (slot / ".runtime-input-origins.json").read_bytes()
            marker = adapter.runtime.marker_path(root, slot.name)
            before = marker.read_bytes(), marker.stat().st_mtime_ns
            assert (slot / "payload" / "runtime-source.json").is_file()
            assert json.loads(receipt)["wheel_inputs"] == wheels.receipt()
        with adapter.candidate(payload, root, **options) as reused:
            assert reused.reused is True and reused.slot == slot
            assert (slot / ".runtime-input-origins.json").read_bytes() == receipt
            assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
        assert not (root / "current-version").exists()
        adapter.activate(root, slot.name, expected=None)
        selectors = {
            name: (root / name).read_bytes() for name in ("current-version", "last-known-good")
        }
        changed = replace(wheels, binding_json='{"fixture":"changed-policy"}')
        visited = []

        def refuse(candidate):
            visited.append(candidate)
            raise RuntimeError("fixture publication policy changed")

        with pytest.raises(RuntimeError, match="policy changed"), adapter.candidate(
            payload, root, **(options | {"wheel_inputs": changed, "validate_candidate": refuse}),
        ):
            pytest.fail("failed candidate became ready")
        assert len(visited) == 1 and visited[0] != slot
        assert not adapter.runtime.is_complete(root, visited[0].name)
        missing = replace(wheels, distributions=tuple(
            ("foreign" if name == "copilot-extensions-worktree-manager" else name, version, filename, digest)
            for name, version, filename, digest in wheels.distributions
        ))
        with pytest.raises(ValueError, match="local dependency graph"), adapter.candidate(
            payload, root, **(options | {"wheel_inputs": missing}),
        ):
            pytest.fail("missing first-party wheel reached construction")
        assert {name: (root / name).read_bytes() for name in selectors} == selectors
        assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
