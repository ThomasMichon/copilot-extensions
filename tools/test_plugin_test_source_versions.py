"""Real owner-allocated source metadata and retained dependency constraints."""
from __future__ import annotations

import json
import tarfile
from contextlib import nullcontext
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import pytest

from test_plugin_test_cache import Graph, released_graph as _released_graph
from test_run_plugin_tests import runner
import plugin_test_cache as cache
import plugin_test_source_versions as versions
from plugin_test_owned_scratch import preparation_scope
from plugin_test_containment import TestSandbox as Sandbox, Limits
from promotion_versions import prepare_versions

TOOLS = Path(__file__).parent
released_graph = _released_graph


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgsign=false",
         "-c", "user.name=Source Fixture", "-c", "user.email=fixture@example.com", *args],
        cwd=root, check=True, capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30,
    ).stdout.strip()


def pin(root: Path) -> str:
    git(root, "add", ".")
    git(root, "commit", "-qm", "Pin diagnostic source inputs")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def candidate(released_graph: Graph) -> Graph:
    plugin, fleet, canonical = released_graph
    root = runner.REPO
    (root / "tools").mkdir()
    for name in (
        "plugin_test_source_versions.py", "plugin_test_cache.py", "promotion_versions.py",
        "accumulate_bumps.py", "changefile.py", "uv_editable_ref.py",
        "promotion_source_archive.py", "launch_wrapper_assets_ref.py",
    ):
        shutil.copyfile(TOOLS / name, root / "tools" / name)
    manifests = [
        canonical.parent / "pyproject.toml",
        plugin / "libs/shared/pyproject.toml", fleet / "libs/shared/pyproject.toml",
    ]
    for path in manifests:
        path.write_text(path.read_text().replace('version = "1.0"', 'version = "0.1.0-dev7"'))
    for project in (plugin, fleet):
        path = project / "pyproject.toml"
        path.write_text(path.read_text().replace("shared>=1", "shared>=0.1.57-dev1"))
    (root / ".changefiles").mkdir()
    consumed = root / ".changefiles/20260101000000-consumed.json"
    consumed.write_text(json.dumps({"changes": [
        {"plugin": "libs/shared", "type": "patch", "comment": "Already consumed"}
    ]}))
    git(root, "init", "-q")
    boundary = pin(root)
    for path in manifests:
        path.write_text(path.read_text().replace("0.1.0-dev7", "0.1.57-dev1"))
    (root / ".github").mkdir(exist_ok=True)
    state = root / ".github/release-pipeline-state.json"
    state.write_text(json.dumps({"last_promotion": {"dev_head": boundary}}))
    main = pin(root)
    git(root, "update-ref", "refs/remotes/origin/main", main)
    for path in manifests:
        path.write_text(path.read_text().replace("0.1.57-dev1", "0.1.0-dev7"))
    (root / ".changefiles/20260102000000-pending.json").write_text(json.dumps({"changes": [
        {"plugin": "libs/shared", "type": "dev", "comment": "Candidate library change"}
    ]}))
    pin(root)
    return plugin, fleet, canonical


def resolve(root: Path, plugin: Path, fleet: Path, selected: Path) -> subprocess.CompletedProcess[str]:
    uv = shutil.which("uv")
    assert uv is not None, "Select existing UV; do not install it for this test"
    requirements = selected / "requirements.txt"
    requirements.write_text(f"-e {plugin}\n-e {fleet}\n")
    return subprocess.run(
        [uv, "pip", "compile", "--no-config", "--offline", "--no-build",
         "--python", sys.executable, str(requirements),
         *cache.source_resolution_arguments("agent-pull-requests", plugin, root, selected)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env={**os.environ, "UV_CACHE_DIR": str(selected / "uv-cache"), "UV_PYTHON_DOWNLOADS": "never"},
    )


@pytest.mark.parametrize("above", [False, True])
@pytest.mark.parametrize("layout", ["canonical", "physical"])
def test_real_resolver_keeps_bounds_and_allocation_is_independent(
    candidate: Graph, above: bool, layout: str,
) -> None:
    plugin, fleet, canonical = candidate
    root = runner.REPO
    if layout == "canonical":
        for project in (plugin, fleet):
            manifest = project / "pyproject.toml"
            relative = Path(os.path.relpath(canonical.parent, project)).as_posix()
            manifest.write_text(manifest.read_text().replace('path = "libs/shared"', f'path = "{relative}"'))
        pin(root)
        assert cache.canonical_test_sources("agent-pull-requests", plugin, root) == ({}, ())
    if above:
        for project in (plugin, fleet):
            manifest = project / "pyproject.toml"
            manifest.write_text(manifest.read_text().replace("shared>=0.1.57-dev1", "shared>=0.1.58"))
        pin(root)
    before = {path: path.read_bytes() for path in root.rglob("*.toml")}
    raw = root / "raw"
    raw.mkdir()
    refused = resolve(root, plugin, fleet, raw)
    assert refused.returncode != 0 and "unsatisfiable" in refused.stderr
    retained = root / "retained"
    retained.mkdir()
    deadline = time.monotonic() + 30
    with pytest.raises(subprocess.CalledProcessError) if above else nullcontext():
        with preparation_scope(None, root, Sandbox, retained, deadline=deadline, dir=root.parent) as private:
            prepared, selected, record = cache.prepare_version_sources(
                "agent-pull-requests", plugin, root, retained, deadline=deadline,
            )
            assert record is not None
            assert record["generated_versions"] == {"shared": "0.1.57-dev2"}
            assert record["applicable_changefiles"] == ["20260102000000-pending.json"]
            assert set(record["inputs"]["original_versions"].values()) == {"0.1.0-dev7"}
            for manifest in prepared.rglob("pyproject.toml"):
                data = tomllib.loads(manifest.read_text())
                if data["project"]["name"] == "shared":
                    assert data["project"]["version"] == "0.1.57-dev2"
            result = resolve(prepared, selected, prepared / "fleet-manager", retained)
            assert (result.returncode != 0) == above, result.stderr
            result.check_returncode()
            fingerprint = cache.dependency_fingerprint("agent-pull-requests", plugin, root, ())
            cache.write_preparation_stamp(
                retained, fingerprint, record, private_state=private,
                deadline=deadline, current_fingerprint=fingerprint,
            )
            assert not (retained / ".dep-fingerprint").exists()
    assert all(path.read_bytes() == value for path, value in before.items())
    if not above:
        assert cache.read_preparation_stamp(
            "agent-pull-requests", plugin, root, retained, deadline=time.monotonic() + 30,
        ) == fingerprint
    else:
        assert not (retained / ".dep-fingerprint").exists()


@pytest.mark.parametrize("selected_extra,bound,succeeds", [
    ("FEATURE_NAME", "0.1.57-dev2", True),
    ("feature-name", "0.1.57-dev3", False),
    ("inactive", "0.1.57-dev3", True),
])
def test_bound_extras_preserve_fingerprinting_preparation_and_real_uv_bounds(
    candidate: Graph, selected_extra: str, bound: str, succeeds: bool,
) -> None:
    plugin, fleet, canonical = candidate
    root = runner.REPO
    manifest = plugin / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace(
        "shared>=0.1.57-dev1", f"shared[{selected_extra}]>=0.1.57-dev1",
    ))
    for library in (canonical.parent, plugin / "libs/shared", fleet / "libs/shared"):
        manifest = library / "pyproject.toml"
        dependencies = json.dumps([
            f"shared>={bound}; extra == 'FEATURE_NAME' and python_version >= '3.10'",
            "shared>=9999; extra == 'missing-extra'",
        ])
        manifest.write_text(manifest.read_text().replace(
            'version = "0.1.0-dev7"', f'version = "0.1.0-dev7"\ndependencies = {dependencies}',
        ) + '\n[project.optional-dependencies]\nFEATURE_NAME=[]\ninactive=[]\n')
    pin(root)
    _, constraints = cache.canonical_test_sources("agent-pull-requests", plugin, root)
    assert not any("9999" in value for value in constraints)
    assert any(f"shared>={bound}" in value for value in constraints) == (selected_extra != "inactive")
    fingerprint = runner._dep_fingerprint("agent-pull-requests")
    retained = root / "extra-candidate"
    retained.mkdir()
    prepared, selected, record = cache.prepare_version_sources(
        "agent-pull-requests", plugin, root, retained, deadline=time.monotonic() + 30,
    )
    assert record is not None and record["generated_versions"] == {"shared": "0.1.57-dev2"}
    result = resolve(prepared, selected, prepared / fleet.relative_to(root), retained)
    assert (result.returncode == 0) is succeeds, result.stderr
    if not succeeds:
        assert "unsatisfiable" in result.stderr
    assert runner._dep_fingerprint("agent-pull-requests") == fingerprint
    assert tomllib.loads((canonical.parent / "pyproject.toml").read_text())["project"]["version"] == "0.1.0-dev7"


def test_actual_application_metadata_attributes_sources_without_runner_heuristics(
    tmp_path: Path,
) -> None:
    from copy import deepcopy
    from packaging.requirements import Requirement
    from dependency_age import DeclarationError, inventory, local_source

    source, fixture = Path(__file__).resolve().parents[1], tmp_path / "source"
    consumers = ("agent-dispatch", "agent-index", "agent-pull-requests", "agent-ssh")
    files = [f"plugins/{name}/pyproject.toml" for name in consumers] + ["fleet-manager/pyproject.toml"]
    for relative in files:
        target = fixture / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())
    _, projects = inventory(fixture)
    observed = []
    for name in consumers:
        path = fixture / "plugins" / name / "pyproject.toml"
        data = projects[path]
        for requirements in data["project"].get("optional-dependencies", {}).values():
            for literal in requirements:
                requirement = Requirement(literal)
                if requirement.name not in data["tool"]["copilot-extensions"]["application-sources"]:
                    continue
                assert local_source(requirement, path, projects, fixture)
                observed.append((path, requirement))
                broken = deepcopy(data)
                broken["tool"]["copilot-extensions"]["application-sources"][requirement.name] = (
                    "agent-pull-requests" if requirement.name == "fleet-manager" else "standalone:fleet-manager"
                )
                projects[path] = broken
                with pytest.raises(DeclarationError, match="name"):
                    local_source(requirement, path, projects, fixture)
                projects[path] = data
    assert observed
    (fixture / "fleet-manager/pyproject.toml").unlink()
    path, requirement = next((path, req) for path, req in observed if req.name == "fleet-manager")
    with pytest.raises(DeclarationError, match="project"):
        local_source(requirement, path, projects, fixture)


def test_source_provenance_rechecks_have_bounded_reproducible_work(
    candidate: Graph, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _, _ = candidate
    reads: list[str] = []
    probes: list[str] = []
    ancestry: list[tuple[str, str]] = []
    from promotion_source_archive import git_text, is_ancestor

    original_digest, original_git, original_ancestor = versions._digest, git_text, is_ancestor
    def digest(path: Path) -> str:
        reads.append(str(path))
        return original_digest(path)
    def probe(repo: Path, arguments: list[str], **options: Any) -> str:
        probes.append(arguments[0])
        return original_git(repo, arguments, **options)
    def ancestor(repo: Path, base: str, head: str) -> bool:
        ancestry.append((base, head))
        return original_ancestor(repo, base, head)
    monkeypatch.setattr(versions, "_digest", digest)
    monkeypatch.setattr(versions, "git_text", probe)
    monkeypatch.setattr(versions, "is_ancestor", ancestor)
    samples = []
    previous = None
    for label in ("first-provenance-pass", "repeated-provenance-pass"):
        reads.clear()
        probes.clear()
        ancestry.clear()
        started = time.monotonic()
        inputs = cache.source_version_inputs("agent-pull-requests", plugin, runner.REPO)
        elapsed = time.monotonic() - started
        assert inputs is not None
        assert len(reads) == len(set(reads)) == len(inputs["sources"])
        assert probes == ["rev-parse", "rev-parse", "write-tree", "rev-parse", "rev-parse",
                          "diff", "show", "rev-parse", "ls-files"]
        assert len(ancestry) == 1
        if previous is not None:
            assert inputs == previous
        previous = inputs
        samples.append({"stage": label, "wall_seconds": elapsed, "source_hash_reads": len(reads),
                        "git_probes": len(probes) + len(ancestry)})
    print(json.dumps({"scope": "contained fixture provenance, not full prepare/reuse or installed latency",
                      "samples": samples, "tail_qualification": "unavailable: two samples"}))


def test_projection_and_promotion_share_versions_and_consumed_boundary(candidate: Graph) -> None:
    plugin, _, _ = candidate
    root = runner.REPO
    retained = root / "retained"
    retained.mkdir()
    prepared, _, record = cache.prepare_version_sources(
        "agent-pull-requests", plugin, root, retained, deadline=time.monotonic() + 30,
    )
    assert record is not None
    full = root / "promotion-data"
    shutil.copytree(prepared, full)
    for manifest in full.rglob("pyproject.toml"):
        if tomllib.loads(manifest.read_text())["project"]["name"] == "shared":
            manifest.write_text(manifest.read_text().replace("0.1.57-dev2", "0.1.0-dev7"))
    acc = versions._load(root / "tools/accumulate_bumps.py", "fixture_promotion_acc")
    acc.REPO, acc.PLUGINS_DIR, acc.MARKETPLACE = full, full / "plugins", full / "unused.json"
    cf = versions._load(root / "tools/changefile.py", "fixture_promotion_changes")
    cf.REPO, cf.CHANGEFILES_DIR = full, full / ".changefiles"
    computed, _, pending = prepare_versions(
        full, record["inputs"]["main"], acc=acc, repo=root,
        entries=list(cf.read_changefiles()), last_dev_head=record["inputs"]["last_dev_head"],
    )
    assert computed == {"libs/shared": ("0.1.57-dev1", "0.1.57-dev2")}
    assert [path.name for path, _ in pending] == record["applicable_changefiles"]
    assert tomllib.loads((full / "libs/shared/pyproject.toml").read_text())["project"]["version"] == record["generated_versions"]["shared"]


@pytest.mark.parametrize("change", [
    "body", "build", "tool", "minimum", "main", "consumed",
    "changefile", "marker", "build-resource",
])
def test_source_version_fingerprint_binds_each_input(candidate: Graph, change: str) -> None:
    plugin, _, canonical = candidate
    root = runner.REPO
    before = cache.dependency_fingerprint("agent-pull-requests", plugin, root, ())
    paths = {
        "body": canonical, "build": plugin / "pyproject.toml",
        "tool": root / "tools/accumulate_bumps.py", "minimum": plugin / "pyproject.toml",
        "changefile": root / ".changefiles/20260102000000-pending.json",
        "marker": plugin / "pyproject.toml",
    }
    if change in {"main", "consumed"}:
        state = root / ".github/release-pipeline-state.json"
        if change == "consumed":
            data = json.loads(state.read_text())
            data["last_promotion"]["dev_head"] = git(root, "rev-parse", "HEAD")
            state.write_text(json.dumps(data))
        else:
            state.write_text(state.read_text() + "\n")
        git(root, "add", ".")
        main = git(root, "commit-tree", git(root, "write-tree"), "-p", "HEAD", "-m", "New Main input")
        git(root, "update-ref", "refs/remotes/origin/main", main)
        git(root, "restore", "--staged", ".")
        state.write_text(state.read_text().rstrip() + "\n")
        git(root, "add", ".")
    elif change == "build-resource":
        resource = cache.retained_build_resources("agent-pull-requests", root)[0]
        (resource / "changed-build-input.py").write_text("BUILD_REVISION = 2\n")
        git(root, "add", ".")
    elif change == "changefile":
        path = paths[change]
        data = json.loads(path.read_text())
        data["changes"][0]["comment"] = "Changed source intent identity"
        path.write_text(json.dumps(data))
        git(root, "add", ".")
    else:
        path = paths[change]
        text = path.read_text()
        path.write_text(
            text.replace("shared>=0.1.57-dev1", "shared>=0.1.57-dev2") if change == "minimum"
            else text.replace("inactive-test-platform", sys.platform) if change == "marker"
            else text + "\n# Changed diagnostic input\n"
        )
        git(root, "add", ".")
    assert cache.dependency_fingerprint("agent-pull-requests", plugin, root, ()) != before


def test_missing_history_deadline_and_partial_inputs_refuse(candidate: Graph) -> None:
    plugin, _, _ = candidate
    root = runner.REPO
    retained = root / "retained"
    retained.mkdir()
    with pytest.raises((TimeoutError, ValueError)):
        cache.prepare_version_sources("agent-pull-requests", plugin, root, retained, deadline=0)
    assert not (retained / ".source-inputs").exists()
    (retained / ".source-inputs").mkdir()
    with pytest.raises(ValueError, match="partial"):
        cache.prepare_version_sources(
            "agent-pull-requests", plugin, root, retained, deadline=time.monotonic() + 30,
        )
    git(root, "update-ref", "-d", "refs/remotes/origin/main")
    with pytest.raises((ValueError, RuntimeError)):
        cache.source_version_inputs("agent-pull-requests", plugin, root)


def test_prepared_input_tampering_and_uncertain_cleanup_never_leave_ready(candidate: Graph) -> None:
    plugin, _, _ = candidate
    root = runner.REPO
    retained = root / "retained"
    retained.mkdir()
    prepared, _, record = cache.prepare_version_sources(
        "agent-pull-requests", plugin, root, retained, deadline=time.monotonic() + 30,
    )
    assert record is not None
    addition = prepared / "libs/shared/injected.py"
    addition.write_text("UNADMITTED = True\n")
    with pytest.raises(ValueError, match="input set"):
        versions.validate_prepared(retained, record, deadline=time.monotonic() + 30)
    addition.unlink()
    manifest = prepared / "libs/shared/pyproject.toml"
    manifest.write_text(manifest.read_text().replace("0.1.57-dev2", "0.1.99-dev1"))
    record["prepared_hashes"][str(manifest.relative_to(prepared))] = versions._digest(manifest)
    with pytest.raises(ValueError, match="metadata differs"):
        versions.validate_prepared(retained, record, deadline=time.monotonic() + 30)
    stamp = retained / ".dep-fingerprint"
    stamp.write_text("must not survive unknown cleanup")
    with pytest.raises(RuntimeError, match="uncertain cleanup"):
        with versions.preparation_ready(retained):
            raise RuntimeError("uncertain cleanup")
    assert not stamp.exists()
    assert prepared.exists()


def test_indirect_source_input_refuses_before_copy(candidate: Graph) -> None:
    plugin, _, canonical = candidate
    target = canonical.parent / "indirect.py"
    try:
        target.symlink_to(canonical)
    except OSError as error:
        pytest.skip(f"native symlink fixture unavailable: {error}")
    git(runner.REPO, "add", ".")
    with pytest.raises(ValueError, match="ordinary|indirect"):
        cache.source_version_inputs("agent-pull-requests", plugin, runner.REPO)


def test_untracked_source_cannot_enter_the_pinned_candidate(candidate: Graph) -> None:
    plugin, _, canonical = candidate
    (canonical.parent / "not-indexed.py").write_text("UNTRACKED = True\n")
    with pytest.raises(ValueError, match="staged and pinned"):
        cache.source_version_inputs("agent-pull-requests", plugin, runner.REPO)


@pytest.mark.parametrize("delete_tracked", [False, True])
@pytest.mark.skipif(sys.platform != "linux", reason="the existing devcontainer snapshot owner is Linux-only")
def test_snapshot_carries_main_and_pins_staged_source_without_host_index(
    candidate: Graph, monkeypatch: pytest.MonkeyPatch, delete_tracked: bool,
) -> None:
    import run_tests_in_devcontainer as snapshot
    plugin, _, canonical = candidate
    root = runner.REPO
    for resource in cache.retained_build_resources("agent-pull-requests", root):
        (resource / "fixture-build-input.txt").write_text("Declared source build input\n")
    ignored = canonical.parent / "_build_info.py"
    ignored.write_text("TRACKED_BUT_IGNORED = True\n")
    ignore = root / ".gitignore"
    ignore.write_text((ignore.read_text() if ignore.exists() else "") + "\n_build_info.py\n")
    git(root, "add", "-f", ignored.relative_to(root).as_posix())
    deleted = canonical.parent / "removed.py"
    deleted.write_text("REMOVED = True\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "source before staged deletion")
    (canonical.parent / "staged-new.py").write_text("STAGED_SOURCE = True\n")
    canonical.write_text(canonical.read_text() + "\n# Current staged source body\n")
    non_utf8 = root / os.fsdecode(b"tracked-\xff.dat")
    non_utf8.write_bytes(b"filesystem-name-roundtrip")
    if delete_tracked:
        deleted.unlink()
    git(root, "add", ".")
    monkeypatch.setattr(snapshot, "REPO", root)
    archive = root / "snapshot.tar"
    snapshot._write_tar_of_repo(archive, ["agent-pull-requests"], include_untracked=False)
    exported = root / "exported"
    exported.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(exported)
    assert git(exported, "rev-parse", "refs/ce-test/pinned-main") == git(root, "rev-parse", "origin/main")
    assert git(exported, "remote") == ""
    assert (exported / ignored.relative_to(root)).read_bytes() == ignored.read_bytes()
    assert (exported / non_utf8.name).read_bytes() == b"filesystem-name-roundtrip"
    inputs = cache.source_version_inputs(
        "agent-pull-requests", exported / plugin.relative_to(root), exported,
    )
    assert inputs is not None
    assert "libs/shared/staged-new.py" in inputs["sources"]
    assert ("libs/shared/removed.py" in inputs["sources"]) != delete_tracked
    assert git(exported, "diff", "--name-only", "HEAD")


@pytest.mark.skipif(sys.platform != "linux", reason="the existing Linux CI tooling-preparation lane supplies the admitted cache")
def test_direct_runner_without_site_packages_uses_seasoned_tooling_owner() -> None:
    result = subprocess.run(
        [sys.executable, "-S", str(TOOLS / "run-plugin-tests.py"), "agent-pull-requests", "--list"],
        cwd=TOOLS.parent, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["agent-pull-requests"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process-group settlement control")
def test_preparation_timeout_settles_a_live_build_backend_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin_test_owned_scratch as owner
    import source_preparation_process
    uv = shutil.which("uv")
    assert uv is not None
    environment = {
        **os.environ, "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never",
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_CREDENTIALS_DIR": str(tmp_path / "credentials"),
    }
    venv = tmp_path / "venv"
    source_preparation_process.run(
        [uv, "venv", "--python", sys.executable, str(venv)],
        deadline=time.monotonic() + 30, env=environment, check=True,
    )
    project = tmp_path / "backend-project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="backend-fixture"\nversion="0.0.0"\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n',
    )
    pid_file = tmp_path / "backend.pid"
    (project / "backend.py").write_text(
        "import pathlib, subprocess, sys, time\n"
        "def build_wheel(*args, **kwargs):\n"
        " child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))\n"
        f" pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        " time.sleep(60)\n",
    )
    deadline = time.monotonic() + 3
    with pytest.raises(subprocess.CalledProcessError) as timeout:
        with preparation_scope(None, tmp_path, Sandbox, venv, deadline=deadline, dir=tmp_path) as private:
            owner.run_preparation_install(
                uv, ["--python", str(venv / "bin/python"), str(project)],
                deadline=deadline, env=environment, cwd=project,
                trusted_source_roots=(tmp_path,),
                limits=Limits(wall_seconds=3), sandbox=Path(private),
            )
    assert timeout.value.returncode == 124
    process = Path("/proc") / pid_file.read_text() / "stat"
    assert not process.exists() or process.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    assert not (venv / ".dep-fingerprint.pending").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows Job/handle settlement")
@pytest.mark.parametrize("delayed_startup", [False, True])
def test_absolute_preparation_deadline_retires_the_actual_windows_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delayed_startup: bool,
) -> None:
    import plugin_test_containment as containment
    import plugin_test_owned_scratch as owner

    with Sandbox(prefix="absolute-prep-", dir=tmp_path) as folder:
        private = Path(folder)
        held = private / "held"
        program = (
            "import pathlib,subprocess,sys,time;"
            "child='import sys,time; f=open(sys.argv[1],\"w\"); f.write(\"ready\");"
            "f.flush(); time.sleep(60)';"
            "subprocess.Popen([sys.executable,'-c',child,sys.argv[1]],"
            "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));"
            "time.sleep(60)"
        )
        original = containment.subprocess.Popen
        if delayed_startup:
            def start(*args: Any, **kwargs: Any) -> Any:
                time.sleep(4.5)
                return original(*args, **kwargs)
            monkeypatch.setattr(containment.subprocess, "Popen", start)
        began = time.monotonic()
        deadline = began + 8
        with pytest.raises(subprocess.CalledProcessError) as timeout:
            owner.run_preparation_command(
                [sys.executable, "-c", program, str(held)], cwd=private,
                env=containment.isolated_environment(os.environ, private),
                sandbox=private, deadline=deadline,
                limits=Limits(wall_seconds=8, max_processes=8, max_memory_mb=256, max_temp_mb=32),
            )
        assert timeout.value.returncode == 124
        assert time.monotonic() <= deadline
        if delayed_startup:
            assert not held.exists(), "work was released after its startup budget expired"
        else:
            assert held.read_text() == "ready"
            held.unlink()
            assert not held.exists(), "the original Job still holds its descendant's file"


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows delete-sharing refusal")
def test_preparation_cleanup_deadline_retains_an_actual_windows_sharing_failure(tmp_path: Path) -> None:
    import plugin_test_containment as containment

    deadline = time.monotonic() + 0.25
    scope = Sandbox(dir=tmp_path, cleanup_deadline=deadline)
    held = Path(scope.name) / "held"
    held.write_bytes(b"fixture")
    handle = held.open("rb")
    try:
        with pytest.raises(containment.SandboxCleanupError, match="original deadline"):
            with scope:
                pass
        assert scope.retained and held.exists()
    finally:
        handle.close()
        scope.cleanup()
    assert not Path(scope.name).exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="actual Darwin process census")
def test_preparation_deadline_settles_the_actual_darwin_group(tmp_path: Path) -> None:
    import plugin_test_containment as containment
    import plugin_test_owned_scratch as owner

    with Sandbox(dir=tmp_path) as folder:
        private = Path(folder)
        group = private / "group"
        program = (
            "import os,pathlib,subprocess,sys,time;"
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpgrp()));time.sleep(60)"
        )
        deadline = time.monotonic() + 8
        with pytest.raises(subprocess.CalledProcessError) as timeout:
            owner.run_preparation_command(
                [sys.executable, "-c", program, str(group)], cwd=private,
                env=containment.isolated_environment(os.environ, private),
                sandbox=private, deadline=deadline,
                limits=Limits(wall_seconds=8, max_processes=8, max_memory_mb=256, max_temp_mb=32),
            )
        assert timeout.value.returncode == 124
        assert time.monotonic() <= deadline
        assert containment._posix_live_group_usage(int(group.read_text())) == (0, 0)


@pytest.mark.skipif(sys.platform != "linux", reason="Git's different-owner safety diagnostic")
def test_preparation_readers_scope_safe_directory_without_inherited_config(
    candidate: Graph, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import promotion_source_archive as source_io
    root = runner.REPO
    diagnostic = {
        "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    }
    environment = {**source_io.git_environment(), **diagnostic}
    rejected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, env=environment,
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert rejected.returncode != 0 and "dubious ownership" in rejected.stderr
    original = source_io.run_composition_child
    def run(arguments: list[str], **options: Any) -> subprocess.CompletedProcess[Any]:
        options["env"] = {**options["env"], **diagnostic}
        return original(arguments, **options)
    monkeypatch.setattr(source_io, "run_composition_child", run)
    head = source_io.git_text(root, ["rev-parse", "HEAD"])
    assert source_io._git(root, ["rev-parse", "HEAD"]).decode().strip() == head
    assert source_io.is_ancestor(root, head, head)
    change = next((root / ".changefiles").glob("*.json"))
    assert source_io.changefile_existed_at(head, change.name, repo=root)
