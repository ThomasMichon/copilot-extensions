from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
TOOLS = REPOSITORY / "tools"
sys.path.insert(0, str(TOOLS))
import prepare_runtime_source as source_runtime

PLUGIN = "agent-fixture"


@pytest.mark.skipif(os.name != "nt", reason="Windows transient rename locks")
@pytest.mark.parametrize("failures", [1, 3, 4])
def test_prepared_publication_retries_only_bounded_transient_locks(tmp_path, monkeypatch, failures):
    payload, destination = tmp_path / "payload", tmp_path / "published"
    payload.mkdir()
    (payload / "selected.txt").write_text("selected")
    actual = Path.rename
    attempts, sleeps = [], []

    def locked(path, target):
        attempts.append((path, target))
        if len(attempts) <= failures:
            raise PermissionError("synthetic transient sharing lock")
        return actual(path, target)

    monkeypatch.setattr(Path, "rename", locked)
    monkeypatch.setattr(source_runtime.time, "sleep", sleeps.append)
    if failures < 4:
        source_runtime._rename_prepared(payload, destination, deadline=time.monotonic() + 10)
        assert (destination / "selected.txt").read_text() == "selected"
        assert len(attempts) == failures + 1
    else:
        with pytest.raises(PermissionError):
            source_runtime._rename_prepared(payload, destination, deadline=time.monotonic() + 10)
        assert not destination.exists() and (payload / "selected.txt").is_file()
        assert len(attempts) == 4
    assert len(sleeps) == min(failures, 3)


def test_prepared_publication_never_retries_permanent_or_expired_errors(tmp_path, monkeypatch):
    attempts = []

    def refuse(path, target):
        attempts.append(path)
        raise FileExistsError("foreign destination")

    monkeypatch.setattr(Path, "rename", refuse)
    monkeypatch.setattr(source_runtime.time, "sleep", lambda *args: pytest.fail("permanent error retried"))
    with pytest.raises(FileExistsError):
        source_runtime._rename_prepared(tmp_path / "input", tmp_path / "output", deadline=time.monotonic() + 10)
    assert len(attempts) == 1
    with pytest.raises(source_runtime.SourcePreparationError, match="deadline"):
        source_runtime._rename_prepared(tmp_path / "input", tmp_path / "output", deadline=time.monotonic() - 1)
    assert len(attempts) == 1


def _git(root: Path, *arguments: str) -> str:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True, text=True, encoding="utf-8", check=True,
        timeout=20, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ).stdout.strip()


def _merged_commit(root: Path) -> str:
    _git(root, "add", "--", ".")
    _git(root, "commit", "-qm", "Synthetic source fixture")
    commit = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/dev", commit)
    return commit


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "--initial-branch=dev")
    _git(root, "config", "user.name", "Example")
    _git(root, "config", "user.email", "user@example.com")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.longpaths", "true")
    (root / ".gitignore").write_text(".cache/\n", encoding="utf-8")
    plugin = root / "plugins" / PLUGIN
    (plugin / "src" / "agent_bridge").mkdir(parents=True)
    (plugin / "src" / "agent_bridge" / "__init__.py").write_text(
        "VALUE = 1\n", encoding="utf-8",
    )
    (plugin / "plugin.json").write_text(
        json.dumps({"name": PLUGIN, "version": "0.4.1-dev3"}), encoding="utf-8",
    )
    (plugin / "pyproject.toml").write_text(
        f'[project]\nname="{PLUGIN}"\nversion="0.4.1-dev3"\n', encoding="utf-8",
    )
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "install.sh").write_text(
        "#!/bin/sh\nexit 0\n", encoding="utf-8",
    )
    return root, _merged_commit(root)


def test_prepare_is_noneditable_relocatable_and_reuses_exact_bytes(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    root, commit = repository
    output = tmp_path / "prepared"
    first = source_runtime.prepare(root, commit, PLUGIN, output)
    receipt = first.payload / "runtime-source.json"
    receipt_bytes, modified = receipt.read_bytes(), receipt.stat().st_mtime_ns
    second = source_runtime.prepare(root, commit, PLUGIN, output)
    assert not first.reused and second.reused
    assert first.payload == second.payload
    assert second.identity == first.identity
    assert commit in first.payload.name
    assert first.identity.package_version == "0.4.1-dev3"
    assert receipt.read_bytes() == receipt_bytes
    assert receipt.stat().st_mtime_ns == modified
    assert not _git(root, "status", "--porcelain", "--ignored")
    assert not (first.payload / ".git").exists()
    assert (first.payload / "pyproject.toml").read_bytes() == (
        root / "plugins" / PLUGIN / "pyproject.toml"
    ).read_bytes()


@pytest.mark.parametrize("archive_mask", ["0000", "0002", "0077"])
def test_prepare_retains_safe_executable_modes_independent_of_git_config(
    repository, tmp_path, monkeypatch, archive_mask,
):
    root, _commit = repository
    script = root / "plugins" / PLUGIN / "scripts" / "install.sh"
    script.chmod(0o755)
    _git(root, "update-index", "--chmod=+x", "--", str(script.relative_to(root)))
    commit = _merged_commit(root)
    _git(root, "config", "tar.umask", archive_mask)
    extract = source_runtime._extract_archive
    archived_modes = {}

    def observe_archive(archive, destination, **options):
        with tarfile.open(archive, "r:") as incoming:
            archived_modes.update(
                (member.name, member.mode) for member in incoming if member.isfile()
            )
        return extract(archive, destination, **options)

    monkeypatch.setattr(source_runtime, "_extract_archive", observe_archive)
    prepared = source_runtime.prepare(root, commit, PLUGIN, tmp_path / "prepared")
    assert archived_modes[f"plugins/{PLUGIN}/scripts/install.sh"] == 0o755
    assert archived_modes[f"plugins/{PLUGIN}/pyproject.toml"] == 0o644
    assert (prepared.payload / "scripts/install.sh").read_bytes() == script.read_bytes()
    if os.name != "nt":
        assert (prepared.payload / "scripts/install.sh").stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("deadline", [True, 0, float("nan"), float("inf")])
def test_preparation_deadline_refuses_before_git_or_output(
    repository, tmp_path, monkeypatch, deadline,
):
    monkeypatch.setattr(
        source_runtime, "_git", lambda *args, **options: pytest.fail("invalid deadline ran Git"),
    )
    with pytest.raises(source_runtime.SourcePreparationError, match="deadline"):
        source_runtime.prepare(*repository, PLUGIN, tmp_path / "prepared", deadline=deadline)
    assert not (tmp_path / "prepared").exists()


def test_preparation_git_child_preserves_original_deadline(repository, monkeypatch):
    actual = source_runtime.subprocess.run
    timeouts = []

    def run(command, **options):
        timeouts.append(options["timeout"])
        return actual(command, **options)

    monkeypatch.setattr(source_runtime.subprocess, "run", run)
    assert source_runtime._git(repository[0], "rev-parse", "HEAD", deadline=time.monotonic() + 5) == repository[1]
    assert len(timeouts) == 1 and 0 < timeouts[0] <= 5


def test_deadline_is_checked_between_archive_copy_chunks(monkeypatch):
    clock = iter((1.0, 3.0))
    monkeypatch.setattr(source_runtime.time, "monotonic", lambda: next(clock))
    copied = io.BytesIO()
    with pytest.raises(source_runtime.SourcePreparationError, match="deadline"):
        source_runtime._copy(io.BytesIO(b"x" * ((1 << 20) + 1)), copied, deadline=2.0)
    assert len(copied.getvalue()) == 1 << 20


def test_same_package_version_new_commit_gets_another_generation(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    root, first_commit = repository
    first = source_runtime.prepare(root, first_commit, PLUGIN, tmp_path / "prepared")
    module = root / "plugins" / PLUGIN / "src" / "agent_bridge" / "__init__.py"
    module.write_text("VALUE = 2\n", encoding="utf-8")
    second_commit = _merged_commit(root)
    second = source_runtime.prepare(root, second_commit, PLUGIN, tmp_path / "prepared")
    assert first.payload != second.payload
    assert first.identity.package_version == second.identity.package_version
    assert "VALUE = 1" in (first.payload / "src" / "agent_bridge" / "__init__.py").read_text()
    assert "VALUE = 2" in (second.payload / "src" / "agent_bridge" / "__init__.py").read_text()


@pytest.mark.parametrize("kind", ["modified", "untracked", "ignored"])
def test_prepare_rejects_nonclean_source(
    repository: tuple[Path, str], tmp_path: Path, kind: str,
) -> None:
    root, commit = repository
    if kind == "modified":
        target = root / ".gitignore"
    elif kind == "ignored":
        target = root / ".cache" / "entry"
        target.parent.mkdir()
    else:
        target = root / "untracked.txt"
    target.write_text("changed", encoding="utf-8")
    with pytest.raises(source_runtime.SourcePreparationError, match="modified, untracked or ignored"):
        source_runtime.prepare(root, commit, PLUGIN, tmp_path / "prepared")


def test_prepare_rejects_unmerged_commit(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    root, merged = repository
    (root / "unmerged.txt").write_text("not merged", encoding="utf-8")
    _git(root, "add", "--", "unmerged.txt")
    _git(root, "commit", "-qm", "Unmerged fixture")
    commit = _git(root, "rev-parse", "HEAD")
    assert commit != merged
    with pytest.raises(source_runtime.SourcePreparationError, match="not merged"):
        source_runtime.prepare(root, commit, PLUGIN, tmp_path / "prepared")


def test_prepare_rejects_worktree_and_in_checkout_output(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    root, commit = repository
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {root / '.git'}\n", encoding="utf-8")
    with pytest.raises(source_runtime.SourcePreparationError, match="not a worktree"):
        source_runtime.prepare(linked, commit, PLUGIN, tmp_path / "prepared")
    with pytest.raises(source_runtime.SourcePreparationError, match="outside the checkout"):
        source_runtime.prepare(root, commit, PLUGIN, root / "generated")
    assert not (root / "generated").exists()


def test_inherited_git_selectors_cannot_redirect_admission(
    repository: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, commit = repository
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "foreign"))
    prepared = source_runtime.prepare(root, commit, PLUGIN, tmp_path / "prepared")
    assert prepared.identity.source_commit == commit


def test_prepare_rejects_publication_root_that_is_the_checkout(
    repository: tuple[Path, str],
) -> None:
    root, commit = repository
    with pytest.raises(source_runtime.SourcePreparationError, match="outside the checkout"):
        source_runtime.prepare(root, commit, root.name, root.parent)
    assert not _git(root, "status", "--porcelain", "--ignored")
    assert not (root / "construction-locks").exists()


def test_prepare_materializes_canonical_vendor_content(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    root, _ = repository
    library = root / "libs" / "example"
    (library / "src" / "example").mkdir(parents=True)
    (library / "src" / "example" / "__init__.py").write_text("VALUE = 7\n", encoding="utf-8")
    (library / "pyproject.toml").write_text(
        '[project]\nname="agent-example"\nversion="1.0.0"\n', encoding="utf-8",
    )
    project = root / "plugins" / PLUGIN / "pyproject.toml"
    project.write_text(
        project.read_text() +
        '\n[tool.uv.sources]\nagent-example = { path="../../libs/example", editable=true }\n',
        encoding="utf-8",
    )
    commit = _merged_commit(root)
    prepared = source_runtime.prepare(root, commit, PLUGIN, tmp_path / "prepared")
    assert (prepared.payload / "libs" / "example" / "src" / "example" / "__init__.py").is_file()
    materialized = (prepared.payload / "pyproject.toml").read_text()
    assert "../../libs/example" not in materialized
    assert "editable" not in materialized
    assert not _git(root, "status", "--porcelain", "--ignored")


def _manager_source(root: Path) -> Path:
    manager = root / "worktree-manager"
    (manager / "src" / "worktree_manager").mkdir(parents=True)
    (manager / "src" / "worktree_manager" / "__init__.py").write_text("VALUE = 3\n")
    (manager / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="0.1.0-dev97"\n'
        '[tool.uv.sources]\nagent-example={path="../libs/example", editable=true}\n',
        encoding="utf-8",
    )
    library = root / "libs" / "example"
    (library / "src" / "example").mkdir(parents=True)
    (library / "src" / "example" / "__init__.py").write_text("VALUE = 7\n")
    (library / "pyproject.toml").write_text(
        '[project]\nname="agent-example"\nversion="1.0.0"\n', encoding="utf-8",
    )
    shutil.copyfile(
        REPOSITORY / "worktree-manager" / "packaged-assets.json",
        manager / "packaged-assets.json",
    )
    tools = root / "tools"
    tools.mkdir()
    manifest = json.loads((manager / "packaged-assets.json").read_text())
    for name in manifest["files"]:
        (tools / name).write_text("raise RuntimeError('fetched tool must not execute')\n")
    return manager


@pytest.mark.parametrize("retained", [False, True])
def test_shipped_trusted_toolkit_prepares_manager_without_executing_fetched_tools(
    repository: tuple[Path, str], tmp_path: Path, retained: bool,
) -> None:
    root, _ = repository
    manager = _manager_source(root)
    if retained:
        report = source_runtime.materializer.materialize_launch_wrapper_assets_into(
            source_consumer_dir=manager, dest_consumer_dir=manager, canonical_root=root,
        )
        assert report and all(line.startswith("OK") for line in report)
    commit = _merged_commit(root)
    installed = tmp_path / "installed-manager"
    installed.mkdir()
    shutil.copyfile(manager / "packaged-assets.json", installed / "packaged-assets.json")
    report = source_runtime.materializer.materialize_launch_wrapper_assets_into(
        source_consumer_dir=installed, dest_consumer_dir=installed, canonical_root=REPOSITORY,
    )
    assert report and all(line.startswith("OK") for line in report)
    scripts = installed / "scripts"
    for helper in (REPOSITORY / "libs" / "versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(helper, scripts / helper.name)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "materialize_main.py").write_text("raise RuntimeError('cwd import')\n")
    command = [
        sys.executable, "-I", "-B",
        str(scripts / "runtime-source-tools" / "prepare_runtime_source.py"),
        "--source", str(root), "--commit", commit, "--plugin", "worktree-manager",
        "--output", str(tmp_path / "prepared"),
    ]
    documents = []
    for _ in range(2):
        result = subprocess.run(
            command, cwd=unrelated, capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr
        documents.append(json.loads(result.stdout))
    assert [document["reused"] for document in documents] == [False, True]
    assert documents[0]["identity"] == documents[1]["identity"]
    payload = Path(documents[0]["payload"])
    identity = source_runtime.read_source_identity(
        payload, "0.1.0-dev97", plugin="worktree-manager",
    )
    assert identity is not None and identity.source_commit == commit
    assert not (payload / "plugin.json").exists()
    assert (payload / "libs" / "example" / "src" / "example" / "__init__.py").is_file()
    assert "editable" not in (payload / "pyproject.toml").read_text()
    assert (payload / "scripts" / "runtime-source-tools" / "prepare_runtime_source.py").read_bytes() == (
        root / "tools" / "prepare_runtime_source.py"
    ).read_bytes()
    assert not _git(root, "status", "--porcelain", "--ignored")


@pytest.mark.parametrize("kind", ["project-name", "retained-asset"])
def test_prepare_manager_refuses_mismatched_identity_or_retained_asset(
    repository: tuple[Path, str], tmp_path: Path, kind: str,
) -> None:
    root, _ = repository
    manager = _manager_source(root)
    if kind == "project-name":
        project = manager / "pyproject.toml"
        project.write_text(project.read_text().replace(
            "copilot-extensions-worktree-manager", "unrelated-project",
        ))
        message = "project identity"
    else:
        toolkit = manager / "scripts" / "runtime-source-tools"
        toolkit.mkdir(parents=True)
        (toolkit / "prepare_runtime_source.py").write_text("different assembly\n")
        message = "already exists"
    commit = _merged_commit(root)
    with pytest.raises(source_runtime.SourcePreparationError, match=message):
        source_runtime.prepare(root, commit, "worktree-manager", tmp_path / "prepared")
    assert not list((tmp_path / "prepared").rglob("runtime-source.json"))


def test_prepared_generation_refuses_changed_content(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    root, commit = repository
    prepared = source_runtime.prepare(root, commit, PLUGIN, tmp_path / "prepared")
    (prepared.payload / "src" / "agent_bridge" / "__init__.py").write_text(
        "VALUE = 99\n", encoding="utf-8",
    )
    with pytest.raises(source_runtime.SourcePreparationError, match="conflicts"):
        source_runtime.prepare(root, commit, PLUGIN, tmp_path / "prepared")


@pytest.mark.parametrize("metadata", ["runtime-source.json", "snapshot-provenance.json"])
def test_prepare_does_not_overwrite_source_owned_deployment_metadata(
    repository: tuple[Path, str], tmp_path: Path, metadata: str,
) -> None:
    root, _ = repository
    (root / "plugins" / PLUGIN / metadata).write_text("{}", encoding="utf-8")
    commit = _merged_commit(root)
    with pytest.raises(source_runtime.SourcePreparationError, match="reserved deployment metadata"):
        source_runtime.prepare(root, commit, PLUGIN, tmp_path / "prepared")


def test_concurrent_preparation_has_one_publication(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    root, commit = repository
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(source_runtime.prepare, root, commit, PLUGIN, tmp_path / "prepared")
            for _ in range(2)
        ]
        results = [future.result(timeout=25) for future in futures]
    assert results[0].payload == results[1].payload
    assert sorted(result.reused for result in results) == [False, True]


@pytest.mark.parametrize("name", ["../outside", "/outside", r"..\outside", "file:stream"])
def test_archive_refuses_unsafe_member_names(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as output:
        member = tarfile.TarInfo(name)
        member.size = 3
        output.addfile(member, io.BytesIO(b"bad"))
    destination = tmp_path / "source"
    destination.mkdir()
    with pytest.raises(source_runtime.SourcePreparationError, match="unsafe"):
        source_runtime._extract_archive(archive, destination)
    assert not list(destination.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="native Windows window observation")
def test_windowless_parent_prepares_and_reuses_without_console_or_focus(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    root, commit = repository
    repository_root = REPOSITORY
    sys.path.insert(0, str(repository_root / "libs" / "agent-procutil" / "src"))
    observer_path = (
        repository_root / "libs" / "agent-procutil" / "tests"
        / "test_spawn_daemon_windows_integration.py"
    )
    spec = importlib.util.spec_from_file_location("source_runtime_window_observer", observer_path)
    assert spec is not None and spec.loader is not None
    observer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = observer
    spec.loader.exec_module(observer)
    observer._SURFACED_PROCESS_NAMES.add("git.exe")
    user32, kernel32, callback = observer._configure_win32()
    pythonw = Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")
    assert pythonw.is_file(), "native GUI-subsystem Python is required for this regression"
    result_path = tmp_path / "windowless-result.json"
    request = tmp_path / "windowless-request.json"
    request.write_text(json.dumps({
        "tools": str(TOOLS),
        "source": str(root), "commit": commit, "plugin": PLUGIN,
        "output": str(tmp_path / "prepared"), "result": str(result_path),
    }), encoding="utf-8")
    parent = tmp_path / "windowless-parent.py"
    parent.write_text(
        "import json, pathlib, sys\n"
        "request = json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
        "sys.path.insert(0, request['tools'])\n"
        "import prepare_runtime_source\n"
        "results = []\n"
        "for cycle in range(2):\n"
        "    prepared = prepare_runtime_source.prepare(\n"
        "        pathlib.Path(request['source']), request['commit'], request['plugin'],\n"
        "        pathlib.Path(request['output']))\n"
        "    results.append({'reused': prepared.reused, 'version': prepared.identity.runtime_version})\n"
        "pathlib.Path(request['result']).write_text(json.dumps(results))\n",
        encoding="utf-8",
    )
    log = tmp_path / "windowless-parent.log"
    with log.open("w", encoding="utf-8") as output:
        with observer._WindowObserver(user32, kernel32, callback) as windows:
            process = subprocess.Popen(
                [str(pythonw), "-I", "-B", str(parent), str(request)],
                stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            returncode = process.wait(timeout=25)
    assert returncode == 0, log.read_text()
    results = json.loads(result_path.read_text())
    assert [result["reused"] for result in results] == [False, True]
    assert results[0]["version"] == results[1]["version"]
    assert not windows.surfaced, windows.surfaced
    assert not windows.focus_changes, windows.focus_changes
