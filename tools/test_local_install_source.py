"""Local preparation freezes Git inputs without consuming release changefiles."""
from contextlib import contextmanager
import os
import hashlib
import io
import json
import re
from pathlib import Path
import subprocess
import shutil
import sys
import tarfile
import time
import tomllib
from types import SimpleNamespace
from typing import Any

import pytest

import local_install_source as local
import promote_release


def test_isolated_provider_cli_loads_owned_siblings_not_caller_modules(tmp_path):
    (tmp_path / "prepare_runtime_source.py").write_text(
        'raise AssertionError("caller module must not replace the owned preparer")\n',
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(Path(local.__file__).resolve()), "--help"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--prepare-applications" in result.stdout


@pytest.mark.parametrize("shared", ["parent", "candidate"])
@pytest.mark.skipif(os.name == "nt", reason="POSIX access bits; Windows uses native DACL checks")
def test_automatic_reuse_never_executes_a_shared_candidate(tmp_path, monkeypatch, shared):
    import build_toolchain_lock as locks

    previous = tmp_path / "previous"
    previous.mkdir(mode=0o700)
    (previous / "inputs").mkdir()
    record = {"schema": local.SCHEMA, "version": 1, "projects": {}}
    (previous / "inputs" / local.MANIFEST).write_text(json.dumps(record))
    (previous / "applications.json").write_text(json.dumps({
        "schema": "copilot-extensions.prepared-applications", "version": 1,
        "snapshots": {"demo": "candidate-controlled"},
    }))
    (tmp_path if shared == "parent" else previous).chmod(0o777)
    monkeypatch.setattr(locks, "resolve_toolchain_lock", lambda *a, **kw: pytest.fail(
        "shared preparation directories cannot supply an executable toolchain",
    ))
    assert local._reused_applications(
        tmp_path / "incoming/inputs", record, tmp_path / "incoming", ("demo",), {},
        uv="unused", deadline=time.monotonic() + 10,
    ) is None


def test_profile_timeout_settles_the_actual_descendant_writer(tmp_path, monkeypatch):
    import build_toolchain_lock as locks

    heartbeat = tmp_path / "heartbeat"
    worker = tmp_path / "profile-worker.py"
    child = (
        "import pathlib,time\np=pathlib.Path(" + repr(str(heartbeat)) + ")\n"
        "while True:\n p.write_text(str(time.monotonic())); time.sleep(.02)\n"
    )
    worker.write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable,'-c'," + repr(child) + "],"
        "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
        "time.sleep(60)\n",
    )
    original = local.preparation_process.run
    deadlines = []

    def run(argv, **options):
        argv[1] = str(worker)
        deadlines.append(options["deadline"])
        return original(argv, **options)

    monkeypatch.setattr(local.preparation_process, "run", run)
    monkeypatch.setattr(locks, "_query_marker_environment", lambda *a, **kw: {})
    deadline = time.monotonic() + 8
    with pytest.raises(subprocess.TimeoutExpired):
        local._prepare_profiles(
            tmp_path, {}, tmp_path / "output", ("demo",), {},
            SimpleNamespace(venv_python=Path(sys.executable), record={}),
            uv="unused", deadline=deadline,
        )
    assert deadlines == [deadline]
    assert time.monotonic() < deadline
    before = heartbeat.read_bytes()
    time.sleep(.1)
    assert heartbeat.read_bytes() == before


@pytest.mark.parametrize("line", [
    b"<!-- VENDOR_POINTER: source=canonical.txt kind=file -->\n",
    b"<!-- VENDOR_POINTER: source=canonical.txt kind=file -->\r\n",
    b"<!-- VENDOR_POINTER: source=canonical.txt kind=file -->\r",
    b"<!-- VENDOR_POINTER: source=canonical.txt kind=file -->",
    b"<!--" + b" " * 16384 + b"VENDOR_POINTER: source=canonical.txt kind=file -->\n",
])
def test_extraction_candidates_match_full_scan_without_reopening_nonmarkers(
    tmp_path, monkeypatch, line,
):
    archive = tmp_path / "source.tar"
    contents = {
        "pointer.unusual": line,
        "canonical.txt": b"canonical bytes\n",
        "empty": b"",
        "ordinary": b"plain text\n",
        "binary": b"\xff\x00\x01",
        "bom": b"\xef\xbb\xbf" + line,
        "later": b"\n" + line,
        "html": b"<html>not a pointer</html>\n",
        "invalid": b"<\xff",
        "VENDOR_POINTER.json": line,
    }
    with tarfile.open(archive, "w") as stream:
        for name, content in contents.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            stream.addfile(member, io.BytesIO(content))
    destination = tmp_path / "source"
    destination.mkdir()
    candidates = []
    local.preparation._extract_archive(
        archive, destination, file_pointer_candidates=candidates,
    )
    expected = local.materialization.find_file_pointers(destination)
    assert expected == [destination / "pointer.unusual"]
    reader = local.materialization._file_pointer_source
    reads = []
    def observe(path):
        reads.append(path.name)
        return reader(path)
    monkeypatch.setattr(local.materialization, "_file_pointer_source", observe)
    assert local.materialization.find_file_pointers(
        destination, candidates=candidates,
    ) == expected
    assert sorted(reads) == ["html", "invalid", "pointer.unusual"]
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == contents
    log = local.materialization.materialize_file_pointers(
        destination, canonical_root=destination, candidates=candidates,
    )
    assert len(log) == 1 and log[0].startswith("OK")
    assert (destination / "pointer.unusual").read_bytes() == contents["canonical.txt"]


@pytest.mark.parametrize("name,kind", [
    ("../escape", tarfile.REGTYPE),
    ("linked", tarfile.SYMTYPE),
    ("hardlink", tarfile.LNKTYPE),
    ("special", tarfile.FIFOTYPE),
])
@pytest.mark.parametrize("scoped", [False, True])
def test_extraction_candidate_collection_still_refuses_unsafe_members(tmp_path, name, kind, scoped):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "outside"
        stream.addfile(member)
    destination = tmp_path / "source"
    destination.mkdir()
    candidates = []
    with pytest.raises(local.preparation.SourcePreparationError):
        local.preparation._extract_archive(
            archive, destination, file_pointer_candidates=candidates,
            **({"include_paths": ("selected",)} if scoped else {}),
        )
    assert candidates == []
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("collision", ["duplicate", "file-directory", "file-parent", "directory-file"])
def test_scoped_extraction_cannot_hide_collisions_in_excluded_members(tmp_path, collision):
    archive = tmp_path / "source.tar"
    entries = {
        "duplicate": [("excluded", tarfile.REGTYPE), ("excluded", tarfile.REGTYPE)],
        "file-directory": [("excluded", tarfile.REGTYPE), ("excluded", tarfile.DIRTYPE)],
        "file-parent": [("excluded", tarfile.REGTYPE), ("excluded/child", tarfile.REGTYPE)],
        "directory-file": [("excluded/child", tarfile.REGTYPE), ("excluded", tarfile.REGTYPE)],
    }[collision]
    with tarfile.open(archive, "w") as stream:
        for name, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = 1 if kind == tarfile.REGTYPE else 0
            stream.addfile(member, io.BytesIO(b"x") if member.isfile() else None)
    destination = tmp_path / "output"
    destination.mkdir()
    with pytest.raises(local.preparation.SourcePreparationError, match="colliding"):
        local.preparation._extract_archive(archive, destination, include_paths=("selected",))
    assert list(destination.iterdir()) == []


def test_scoped_extraction_keeps_platform_case_collision_rules(tmp_path):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as stream:
        for name in ("Excluded", "excluded"):
            member = tarfile.TarInfo(name)
            member.size = 1
            stream.addfile(member, io.BytesIO(b"x"))
    destination = tmp_path / "output"
    destination.mkdir()
    if os.name == "nt":
        with pytest.raises(local.preparation.SourcePreparationError, match="colliding"):
            local.preparation._extract_archive(archive, destination, include_paths=("selected",))
    else:
        local.preparation._extract_archive(archive, destination, include_paths=("selected",))
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("selection", [(), ("",), ("/absolute",), ("../escape",), ("a:b",), ("a\\b",)])
def test_scoped_extraction_refuses_unsafe_selection_before_writing(tmp_path, selection):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w"):
        pass
    destination = tmp_path / "output"
    destination.mkdir()
    with pytest.raises(local.preparation.SourcePreparationError):
        local.preparation._extract_archive(archive, destination, include_paths=selection)
    assert list(destination.iterdir()) == []


def test_scoped_extraction_keeps_only_selected_roots_and_never_replaces_output(tmp_path):
    archive = tmp_path / "source.tar"
    content = b"<!-- VENDOR_POINTER: source=canonical kind=file -->\n"
    with tarfile.open(archive, "w") as stream:
        for name in ("selected/file", "metadata.toml", "excluded/file"):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            stream.addfile(member, io.BytesIO(content))
    destination = tmp_path / "output"
    destination.mkdir()
    candidates = []
    selection = ("selected", "metadata.toml")
    local.preparation._extract_archive(
        archive, destination, include_paths=selection, file_pointer_candidates=candidates,
    )
    assert sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*")
                  if path.is_file()) == ["metadata.toml", "selected/file"]
    assert set(candidates) == {destination / "metadata.toml", destination / "selected/file"}
    with pytest.raises(local.preparation.SourcePreparationError, match="empty destination"):
        local.preparation._extract_archive(archive, destination, include_paths=selection)
    assert (destination / "selected/file").read_bytes() == content


def test_application_export_selection_does_not_import_profile_or_packaging_in_cold_parent():
    program = """
import builtins, json, sys
from pathlib import Path
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "local_install_profile" or name.split(".")[0] == "packaging":
        raise AssertionError("export selection imported worker-only dependencies")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
sys.path.insert(0, sys.argv[1])
import local_install_source as provider
print(json.dumps(provider._application_export_paths(Path(sys.argv[2]), ("agent-containers",))))
"""
    root = Path(local.__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(root / "tools"), str(root)],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "plugins/agent-containers" in json.loads(result.stdout)


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_extraction_preserves_modes_without_repeating_parent_or_writable_windows_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    archive = tmp_path / "source.tar"
    files = [("writable", 0o644), ("executable", 0o755), ("readonly", 0o444), ("nested/file", 0o644)]
    with tarfile.open(archive, "w") as stream:
        for name, mode in files:
            member = tarfile.TarInfo(name)
            member.mode, member.size = mode, 1
            stream.addfile(member, io.BytesIO(b"x"))
    destination = tmp_path / "source"
    destination.mkdir()
    mkdir, chmod = Path.mkdir, Path.chmod
    parents, modes = [], []

    def create(path: Path, *args: Any, **kwargs: Any) -> None:
        parents.append(path)
        return mkdir(path, *args, **kwargs)

    def apply_mode(path: Path, mode: int) -> None:
        modes.append((path.relative_to(destination).as_posix(), mode))
        return chmod(path, mode)

    monkeypatch.setattr(local.preparation, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(Path, "mkdir", create)
    monkeypatch.setattr(Path, "chmod", apply_mode)
    local.preparation._extract_archive(archive, destination)
    assert parents == [destination, destination / "nested"]
    assert modes == (files if platform == "posix" else [("readonly", 0o444)])
    assert all((destination / name).read_bytes() == b"x" for name, _mode in files)


def test_source_composition_candidates_still_refuse_pointer_escape(repository, tmp_path):
    repo, _ = repository
    (repo / "pointer").write_text(
        "<!-- VENDOR_POINTER: source=../outside.txt kind=file -->\n",
    )
    local.preparation._git(repo, "add", "pointer")
    local.preparation._git(
        repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Pointer refusal fixture",
    )
    commit = local.preparation._git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="escapes the canonical root"):
        local.compose(repo, commit, None, tmp_path / "refused")


def test_cli_refuses_public_age_override_before_governed_source_preparation(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(local, "compose", lambda *args, **kwargs: pytest.fail(
        "invalid toolchain options must not archive or materialize the repository",
    ))
    output = tmp_path / "must-not-exist"
    with pytest.raises(SystemExit) as refused:
        local.main([
            "--source-checkout", str(tmp_path), "--source-commit", "a" * 40,
            "--output", str(output), "--toolchain-min-age-days", "7",
        ])
    assert refused.value.code == 2
    assert "--toolchain-source seasoned-public" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("reuse", [False, True])
def test_cli_prepares_only_the_selected_core_projects(tmp_path, monkeypatch, capsys, reuse):
    seen, composed = [], []
    monkeypatch.setattr(
        local, "compose",
        lambda *args, **kwargs: (composed.append(kwargs["projects"]) or {"projects": {}}),
    )
    monkeypatch.setattr(
        local, "build_projects",
        lambda output, record, **kwargs: seen.append(
            (output, kwargs["projects"], kwargs["reuse_source"]),
        ),
    )
    arguments = [
        "--source-checkout", str(tmp_path), "--source-commit", "a" * 40,
        "--output", str(tmp_path / "prepared"),
        "--project", "standalone:worktree-manager", "--project", "agent-worktrees",
        *(["--reuse-install-source", str(tmp_path / "previous")] if reuse else []),
    ]
    assert local.main(arguments) == 0
    assert seen == [
        (tmp_path / "prepared", ("standalone:worktree-manager", "agent-worktrees"),
         tmp_path / "previous" if reuse else None),
    ]
    assert composed == [seen[0][1]]
    with pytest.raises(SystemExit) as refused:
        local.main([*arguments, "--suite"])
    assert refused.value.code == 2
    assert len(seen) == 1
    assert "not allowed with argument" in capsys.readouterr().err
    assert local.main(arguments[:6] + ["--suite"]) == 0
    assert composed[-1] is None and seen[-1][1] is None
    assert local.main(arguments[:6]) == 0
    assert composed[-1] == seen[-1][1] == ("standalone:worktree-manager",)


@pytest.fixture
def repository(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True, creationflags=flags)
    (repository / "version.txt").write_text("released-version\n", encoding="utf-8")
    local.preparation._git(repository, "add", "version.txt")
    local.preparation._git(
        repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Fixture Main",
    )
    return repository, local.preparation._git(repository, "rev-parse", "HEAD")


@pytest.mark.parametrize("budget", ["none", "deadline", "timeout"])
def test_cli_keeps_explicit_uv_and_one_budget_across_source_and_build(
    tmp_path, monkeypatch, budget,
):
    calls = []
    monkeypatch.setattr(local.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(local, "compose", lambda *a, **kw: (
        calls.append(("source", kw)) or {"projects": {}}
    ))
    monkeypatch.setattr(local, "build_projects", lambda *a, **kw: calls.append(("build", kw)))
    uv = tmp_path / "selected-uv"
    flags = {
        "none": [], "deadline": ["--deadline", "140"], "timeout": ["--timeout", "40"],
    }[budget]
    assert local.main([
        "--source-checkout", str(tmp_path), "--source-commit", "a" * 40,
        "--output", str(tmp_path / "prepared"), "--uv", str(uv), *flags,
    ]) == 0
    assert [phase for phase, _options in calls] == ["source", "build"]
    assert calls[0][1]["deadline"] == calls[1][1]["deadline"] == (
        None if budget == "none" else 140.0
    )
    assert calls[1][1]["uv"] == str(uv)
    assert calls[0][1]["projects"] == calls[1][1]["projects"] == ("standalone:worktree-manager",)


@pytest.mark.parametrize("shape", ["legacy", "nested", "groups"])
def test_packaged_source_assets_keep_flat_and_grouped_canonical_paths(tmp_path, shape):
    import launch_wrapper_assets_ref as assets

    group = {
        "canonicalDir": "libs/fixture" if shape == "legacy" else ".",
        "payloadDir": "scripts", "files": ["helper.py" if shape == "legacy" else "tools/helper.py"],
    }
    record = {"schema": "copilot-extensions.packaged-assets", "version": 1 if shape == "legacy" else 2}
    record.update({"groups": [group]} if shape == "groups" else group)
    (tmp_path / assets.PACKAGED_MANIFEST_NAME).write_text(json.dumps(record))
    manifest, = assets.load_manifest_groups(tmp_path, manifest_name=assets.PACKAGED_MANIFEST_NAME)
    assert manifest.source_relative(manifest.files[0]) == (
        "libs/fixture/helper.py" if shape == "legacy" else "tools/helper.py"
    )
    assert manifest.payload_dir() / manifest.files[0] == (
        tmp_path / "scripts" / ("helper.py" if shape == "legacy" else "tools/helper.py")
    )


@pytest.mark.parametrize("damage", ["boolean-version", "root-v1", "case-alias", "file-parent"])
def test_packaged_source_asset_dispatch_preserves_version_and_collision_refusals(tmp_path, damage):
    import launch_wrapper_assets_ref as assets

    groups = [
        {"canonicalDir": ".", "payloadDir": "scripts", "files": ["Tools/helper.py"]},
        {"canonicalDir": ".", "payloadDir": "scripts", "files": [
            "tools/HELPER.py" if damage == "case-alias" else "Tools/helper.py/child.py",
        ]},
    ]
    record = {"schema": "copilot-extensions.packaged-assets", "version": 2, "groups": groups}
    if damage in {"boolean-version", "root-v1"}:
        record = {
            "schema": record["schema"], "version": True if damage == "boolean-version" else 1,
            "canonicalDir": ".", "payloadDir": "scripts", "files": ["helper.py"],
        }
    (tmp_path / assets.PACKAGED_MANIFEST_NAME).write_text(json.dumps(record))
    with pytest.raises(ValueError):
        assets.load_manifest_groups(tmp_path, manifest_name=assets.PACKAGED_MANIFEST_NAME)


def test_main_ancestor_snapshot_keeps_finalized_version_and_does_not_follow_edits(
    repository, tmp_path, monkeypatch,
):
    repo, main = repository
    (repo / "version.txt").write_text("uncommitted newer label\n", encoding="utf-8")
    monkeypatch.setattr(promote_release, "consume_pending_changes", lambda *a, **kw: pytest.fail(
        "a finalized Main ancestor must not be reseeded or relabeled",
    ))
    output = tmp_path / "main-install-source"
    record = local.compose(repo, main, main, output, finalized_main=True)
    assert (output / "source/version.txt").read_text(encoding="utf-8") == "released-version\n"
    assert record["source_commit"] == main and record["main_head"] == main
    assert record["kind"] == "main" and "composition" not in record
    with pytest.raises(ValueError, match="already exists"):
        local.compose(repo, main, main, output, finalized_main=True)
    assert (repo / "version.txt").read_text(encoding="utf-8") == "uncommitted newer label\n"


def test_main_snapshot_uses_portable_archive_eol_and_keeps_explicit_crlf(repository, tmp_path):
    repo, _ = repository
    (repo / ".gitattributes").write_text("* text=auto\n*.cmd text eol=crlf\n", encoding="utf-8")
    (repo / "automatic.txt").write_bytes(b"first\nsecond\n")
    (repo / "explicit.cmd").write_bytes(b"first\nsecond\n")
    (repo / "binary.bin").write_bytes(bytes(range(256)))
    local.preparation._git(repo, "add", ".gitattributes", "automatic.txt", "explicit.cmd", "binary.bin")
    local.preparation._git(
        repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Archive attributes",
    )
    commit = local.preparation._git(repo, "rev-parse", "HEAD")
    local.preparation._git(repo, "config", "core.eol", "crlf")
    output = tmp_path / "prepared"
    local.compose(repo, commit, commit, output, finalized_main=True)
    assert (output / "source" / "automatic.txt").read_bytes() == b"first\nsecond\n"
    assert (output / "source" / "explicit.cmd").read_bytes() == b"first\r\nsecond\r\n"
    assert (output / "source" / "binary.bin").read_bytes() == bytes(range(256))


@pytest.mark.parametrize("relative", [False, True])
def test_native_archive_writes_owned_file_without_captured_binary(
    repository, tmp_path, monkeypatch, relative,
):
    repo, commit = repository
    monkeypatch.chdir(tmp_path)
    archive = Path("native.tar") if relative else tmp_path / "native.tar"
    original = local.preparation_process.run
    observed = []
    deadline = time.monotonic() + 30

    def run(argv, **options):
        assert "--output=" + str(archive.absolute()) in argv
        assert "stdout" not in options and options["capture_output"] is True
        assert options["deadline"] == deadline
        result = original(argv, **options)
        assert not result.stdout
        observed.append(result.returncode)
        return result

    monkeypatch.setattr(local.preparation_process, "run", run)
    local.preparation._archive(repo, commit, archive, deadline=deadline)
    assert observed == [0]
    with tarfile.open(archive) as content:
        stream = content.extractfile("version.txt")
        assert stream is not None and stream.read() == b"released-version\n"
    assert not (repo / "native.tar").exists()


def test_native_archive_refuses_existing_destination_before_launch(tmp_path, monkeypatch):
    archive = tmp_path / "existing.tar"
    archive.write_bytes(b"retained artifact")
    monkeypatch.setattr(local.preparation, "_git",
                        lambda *_a, **_kw: pytest.fail("existing archive was overwritten"))
    with pytest.raises(FileExistsError):
        local.preparation._archive(tmp_path, "unused", archive)
    assert archive.read_bytes() == b"retained artifact"


@pytest.mark.skipif(os.name == "nt", reason="POSIX group permissions; Windows uses native DACL checks")
@pytest.mark.parametrize("shared", ["parent", "ancestor"])
def test_native_archive_refuses_replacement_capable_directories(tmp_path, monkeypatch, shared):
    root = tmp_path / "shared"
    root.mkdir(mode=0o700)
    parent = root / "output"
    parent.mkdir(mode=0o700)
    victim = tmp_path / "retained"
    victim.write_bytes(b"untouched")
    archive = parent / "source.tar"
    archive.symlink_to(victim)
    (parent if shared == "parent" else root).chmod(0o770)
    monkeypatch.setattr(local.preparation, "_git",
                        lambda *_a, **_kw: pytest.fail("replacement-capable path reached Git"))
    with pytest.raises(PermissionError, match="another user's writes"):
        local.preparation._archive(tmp_path, "unused", archive)
    assert victim.read_bytes() == b"untouched" and archive.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX umask; Windows uses private directory creation")
def test_compose_directories_are_created_private_under_group_writable_umask(tmp_path):
    from agent_procutil.owned_process import run_caller_owned

    root = tmp_path / "new" / "output"
    code = (
        "import os,stat,sys\nfrom pathlib import Path\n"
        "sys.path.insert(0,sys.argv[1])\nimport local_install_source as local\n"
        "os.umask(0o002)\n"
        "root=Path(sys.argv[2])\nlocal._private_output_directory(root)\n"
        "assert stat.S_IMODE(root.stat().st_mode)==0o700\n"
        "assert stat.S_IMODE(root.parent.stat().st_mode)==0o700\n"
    )
    result = run_caller_owned(
        [sys.executable, "-c", code, str(Path(local.__file__).parent), str(root)],
        timeout=15, max_stdout_bytes=8192, max_stderr_bytes=8192,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length paths")
@pytest.mark.parametrize(
    ("reference", "problem"),
    [
        ("../second", None),
        ("../missing/../second", "missing path component"),
        ("../ordinary-file/../second", "missing path component"),
        ("../../../example/libs/second", "leaves its owning payload"),
    ],
)
def test_extended_source_paths_validate_each_dependency_component(tmp_path, reference, problem):
    consumer = tmp_path / "example"
    first, second = consumer / "libs" / "first", consumer / "libs" / "second"
    first.mkdir(parents=True)
    second.mkdir()
    (first.parent / "ordinary-file").write_text("not a directory")
    (consumer / "pyproject.toml").write_text('[tool.uv.sources]\nfirst = {path = "libs/first"}\n')
    (first / "pyproject.toml").write_text(f'[tool.uv.sources]\nsecond = {{path = "{reference}"}}\n')
    (second / "pyproject.toml").write_text('[project]\nname = "second"\nversion = "1"\n')
    extended = Path(os.path._getfinalpathname(consumer))
    problems = local.materialization.uer.materialized_local_source_problems(extended)
    if problem is None:
        assert problems == []
    else:
        assert any(problem in item for item in problems)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length paths")
@pytest.mark.parametrize("extended", [False, True])
def test_main_snapshot_extracts_deep_paths_from_normal_or_extended_output(
    repository, tmp_path, extended,
):
    repo, _ = repository
    segment = "nested-" + "x" * 50
    relative = Path(segment) / segment / "payload.txt"
    (repo / relative).parent.mkdir(parents=True)
    (repo / relative).write_bytes(b"selected source")
    local.preparation._git(repo, "add", "--", str(relative))
    local.preparation._git(
        repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Deep source path",
    )
    commit = local.preparation._git(repo, "rev-parse", "HEAD")
    output = tmp_path / ("output-" + "y" * 120)
    native = Path("\\\\?\\" + str(output))
    record = local.compose(repo, commit, commit, native if extended else output, finalized_main=True)
    assert len(str(output / "source" / relative)) > 260
    assert (native / "source" / relative).read_bytes() == b"selected source"
    assert not (native / "source.tar").exists()
    assert record["source_commit"] == commit


def test_development_snapshot_materializes_without_seeding_or_consuming_changefiles(
    repository, tmp_path, monkeypatch,
):
    repo, main = repository
    seen = []
    changefile = repo / ".changefiles" / "pending.json"
    changefile.parent.mkdir()
    changefile.write_text('{"changes":[{"plugin":"worktree-manager","type":"patch"}]}\n')
    local.preparation._git(repo, "add", ".changefiles")
    local.preparation._git(
        repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Pending release change",
    )
    commit = local.preparation._git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(
        promote_release, "consume_pending_changes",
        lambda *a, **kw: pytest.fail("local deployment must not accumulate release versions"),
    )

    def materialize(source, **options):
        assert (source / "version.txt").read_text(encoding="utf-8") == "released-version\n"
        assert options == {"canonical_root": source, "file_pointer_candidates": []}
        seen.append("materialization")
        return []

    monkeypatch.setattr(local.materialization, "materialize", materialize)
    output = tmp_path / "development-install-source"
    record = local.compose(repo, commit, None, output, deadline=time.monotonic() + 30)
    assert seen == ["materialization"]
    assert (output / "source/version.txt").read_text(encoding="utf-8") == "released-version\n"
    assert (output / "source/.changefiles/pending.json").read_bytes() == changefile.read_bytes()
    assert record["source_commit"] == commit and record["main_head"] is None
    assert record["kind"] == "source" and record["projects"] == {}
    assert "composition" not in record


def test_nonancestor_lkg_refuses_before_creating_any_output(repository, tmp_path):
    repo, main = repository
    (repo / "version.txt").write_text("development-version\n", encoding="utf-8")
    local.preparation._git(repo, "add", "version.txt")
    local.preparation._git(
        repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Fixture development",
    )
    newer = local.preparation._git(repo, "rev-parse", "HEAD")
    output = tmp_path / "refused"
    with pytest.raises(ValueError, match="ancestry"):
        local.compose(repo, newer, main, output, finalized_main=True)
    assert not output.exists()


def test_materialization_refusal_never_returns_a_prepared_snapshot(repository, tmp_path, monkeypatch):
    repo, commit = repository
    monkeypatch.setattr(local.materialization, "materialize", lambda *a, **kw: ["SKIP unsafe source"])
    with pytest.raises(ValueError, match="materialization refused"):
        local.compose(repo, commit, None, tmp_path / "refused")


def test_source_change_distinguishes_local_generations_without_a_version_bump(
    repository, tmp_path,
):
    repo, first = repository
    local.preparation._git(repo, "remote", "add", "origin", "https://github.com/Example/Suite.git")
    (repo / "behavior.txt").write_text("new behavior\n", encoding="utf-8")
    local.preparation._git(repo, "add", "behavior.txt")
    local.preparation._git(
        repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Change behavior without changing version",
    )
    second = local.preparation._git(repo, "rev-parse", "HEAD")
    records = [
        local.compose(repo, commit, None, tmp_path / name)
        for commit, name in ((first, "first"), (second, "second"))
    ]
    assert records[0]["source_commit"] != records[1]["source_commit"]
    assert records[0]["source_tree"] != records[1]["source_tree"]
    assert records[0]["source_descriptor"] == records[1]["source_descriptor"] == {
        "kind": "github", "canonical": "github:example/suite", "ref": "",
    }
    for name in ("first", "second"):
        assert (tmp_path / name / "source/version.txt").read_text() == "released-version\n"


def test_local_source_rejects_credential_bearing_repository_before_materialization(repository, tmp_path):
    repo, commit = repository
    local.preparation._git(
        repo, "remote", "add", "origin", "https://fixture:private-value@example.invalid/suite.git",
    )
    output = tmp_path / "refused"
    with pytest.raises(ValueError, match="must not contain credentials") as failure:
        local.compose(repo, commit, None, output)
    assert "private-value" not in str(failure.value)
    assert not output.exists()


def test_local_source_can_retain_explicit_existing_provenance_without_a_remote(repository, tmp_path):
    repo, commit = repository
    record = local.compose(
        repo, commit, None, tmp_path / "explicit",
        source_descriptor={"source": "github", "repo": "Example/Suite"},
    )
    assert record["source_descriptor"] == {
        "kind": "github", "canonical": "github:example/suite", "ref": "",
    }


@pytest.mark.parametrize("deep", [False, True])
@pytest.mark.parametrize("remaining", [None, 60, 240])
@pytest.mark.parametrize(("borrow", "retain_toolchain"), [(False, False), (True, False), (True, True)])
def test_suite_build_preserves_roster_versions_and_uses_one_toolchain(
    tmp_path, monkeypatch, deep, remaining, borrow, retain_toolchain,
):
    import build_python_artifacts
    import build_toolchain_lock
    import promotion_python_artifacts
    import standalone_python_artifacts
    from versioned_runtime_source import payload_package_version, read_source_identity

    if deep and os.name != "nt":
        pytest.skip("Windows extended-length paths")
    base_python = str(tmp_path / "stable-base-python")
    monkeypatch.setattr(local.sys, "_base_executable", base_python)
    if os.name == "nt":
        temp_root = tmp_path / "builder-temp"
        (temp_root / "libs" / "versioned-runtime").mkdir(parents=True)
        monkeypatch.setattr(local.tempfile, "gettempdir", lambda: str(temp_root))
    output = tmp_path / (("prepared-" + "y" * 140) if deep else "prepared")
    native_output = Path(local.native_retained_path(output))
    source = native_output / "source"
    source.mkdir(parents=True)
    (source / "unselected-only.txt").write_text("not a build input")
    for relative in (
        "libs/versioned-runtime", "libs/installer-engine", "libs/agent-procutil/src/agent_procutil",
    ):
        resource = source / relative
        resource.mkdir(parents=True)
        (resource / "fixture.txt").write_text("declared build resource")
    projects = {
        "agent-worktrees": "1.0.0",
        "standalone:worktree-manager": "2.0.0",
        "standalone:fleet-manager": "3.0.0",
        "agent-index": "4.0.0",
        "standalone:index-engine": "5.0.0",
    }
    for key, version in projects.items():
        component = key.split(":")[-1]
        relative = standalone_python_artifacts.COMPONENTS[component] if ":" in key else "plugins/" + component
        payload = source / relative
        payload.mkdir(parents=True)
        name = (
            "copilot-extensions-worktree-manager" if component == "worktree-manager"
            else "agent-index-engine" if component == "index-engine" else component
        )
        (payload / "pyproject.toml").write_text(
            '[build-system]\nrequires=["setuptools>=68"]\n'
            'build-backend="setuptools.build_meta"\n'
            f'[project]\nname="{name}"\nversion="{version}"\n',
        )
        if component == "worktree-manager":
            (payload / "setup.py").write_bytes(
                (standalone_python_artifacts.TRUSTED_ROOT / relative / "setup.py").read_bytes()
            )
        if ":" not in key:
            (payload / "plugin.json").write_text(json.dumps({"name": component, "version": version}))
    monkeypatch.setattr(promotion_python_artifacts, "candidate_projects", lambda root: projects)
    toolchain = SimpleNamespace(record={"fixture": "one-admitted-toolchain"})
    resolutions, builds, build_roots = [], [], []
    def resolve(path, **options):
        resolutions.append((path, options))
        path.mkdir(parents=True, exist_ok=True)
        (path / "retained-marker").write_text("one original toolchain")
        return toolchain

    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", resolve)
    builder = SimpleNamespace(
        __file__=build_python_artifacts.__file__, uer=build_python_artifacts.uer,
        resolve_vendored_libs=lambda root: [],
    )
    wheel_calls = []
    builder.build_wheel = lambda *args, **kwargs: wheel_calls.append(kwargs)
    monkeypatch.setattr(local, "_load_module", lambda *a: builder)
    admitted_toolchain = toolchain
    uv = tmp_path / "uv.exe"
    deadline = time.monotonic() + (remaining or 240)
    execution = {"uv": uv, "deadline": deadline} if remaining is not None else {}

    def build(component, *, out_dir, toolchain, builder=builder, **options):
        assert options == execution
        assert toolchain is admitted_toolchain
        builder.build_wheel(component, out_dir, toolchain=toolchain)
        if os.name == "nt":
            assert builder.REPO != source
            expected = native_output if retain_toolchain else builder.REPO.parent
            assert Path(local.native_retained_path(resolutions[0][0])).parent == expected
            assert not (builder.REPO / "unselected-only.txt").exists()
        else:
            assert builder.REPO == source
        key = "standalone:" + component if "standalone:" + component in projects else component
        relative = standalone_python_artifacts.COMPONENTS[component] if ":" in key else "plugins/" + component
        assert (builder.REPO / relative / "pyproject.toml").read_bytes() == (
            source / relative / "pyproject.toml"
        ).read_bytes()
        if os.name == "nt" and component == "worktree-manager":
            helpers = [
                parent / "libs" / "versioned-runtime"
                for parent in list((builder.REPO / relative).parents)[:3]
                if (parent / "libs" / "versioned-runtime").exists()
            ]
            assert helpers == [builder.REPO / "libs" / "versioned-runtime"]
        if os.name == "nt":
            generated = builder.REPO / relative / "build"
            generated.mkdir(exist_ok=True)
            (generated / "generated.txt").write_text("backend output")
        build_roots.append(builder.REPO)
        builds.append(key)
        return {
            "component" if ":" in key else "plugin": component,
            "version": projects[key], "build_toolchain": toolchain.record,
        }

    builder.build_plugin_artifacts = build
    monkeypatch.setattr(standalone_python_artifacts, "build_standalone_artifacts", build)
    record = {
        "schema": local.SCHEMA, "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40, "projects": {},
    }
    if borrow:
        with local._built_projects(
            output, record, retain_toolchain=retain_toolchain, **execution,
        ) as (observed, borrowed):
            assert observed is record and borrowed is toolchain
            assert build_roots[0].is_dir()
            assert json.loads((native_output / local.MANIFEST).read_text()) == record
    else:
        local.build_projects(output, record, **execution)
    assert builds == list(projects) and len(resolutions) == 1
    assert resolutions[0][1] == {
        "python": base_python, "source_policy": "governed", "min_age_days": None, **execution,
    }
    for call in wheel_calls:
        assert call["toolchain"] is admitted_toolchain
        if remaining is not None:
            assert call["uv"] == str(uv)
            assert time.monotonic() < call["deadline"] <= deadline
            assert call["deadline"] <= time.monotonic() + 180
            if remaining == 60:
                assert call["deadline"] == deadline
        else:
            assert set(call) == {"toolchain"}
    if os.name == "nt":
        assert len(set(build_roots)) == 1
        assert not build_roots[0].exists()
    if retain_toolchain or os.name != "nt":
        assert resolutions[0][0] == native_output / "build-tools"
        assert (resolutions[0][0] / "retained-marker").read_text() == "one original toolchain"
    else:
        assert not resolutions[0][0].exists()
    for project in record["projects"].values():
        payload = native_output / project["payload"]
        component = project["source"]["plugin"]
        version = payload_package_version(payload, component)
        assert read_source_identity(payload, version, plugin=component).document() == project["source"]
        assert project["artifact"]["version"] == version


def test_suite_build_expired_deadline_refuses_before_source_writes(tmp_path, monkeypatch):
    import build_toolchain_lock

    monkeypatch.setattr(
        build_toolchain_lock, "resolve_toolchain_lock",
        lambda *args, **kwargs: pytest.fail("expired caller reached toolchain preparation"),
    )
    output = tmp_path / "not-created"
    with pytest.raises(local.preparation.SourcePreparationError, match="deadline exceeded"):
        local.build_projects(
            output, {"projects": {}}, uv=tmp_path / "uv.exe",
            deadline=time.monotonic() - 1,
        )
    assert not output.exists()


def test_local_builder_load_keeps_controller_and_module_state_independent(tmp_path, monkeypatch):
    import builtins
    import build_python_artifacts

    original_import = builtins.__import__

    def without_controller(name, *args, **kwargs):
        if name == "promote_release":
            pytest.fail("local artifact building imported the release controller")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_controller)
    original_root = build_python_artifacts.REPO
    builder = local._load_module(
        Path(build_python_artifacts.__file__), "_local_install_artifact_builder",
    )
    builder.REPO = tmp_path
    assert build_python_artifacts.REPO == original_root
    assert builder.resolve_vendored_libs is not build_python_artifacts.resolve_vendored_libs


def test_manager_provider_rejects_foreign_native_target_before_profile(tmp_path, monkeypatch):
    import build_toolchain_lock
    import installer_packaging_seed
    import install_source
    import local_install_profile
    import prepare_manager_application

    uv = tmp_path / "uv"
    uv.write_bytes(b"fixture, never executed")
    monkeypatch.setattr(install_source, "select", lambda *a: SimpleNamespace(
        identity=SimpleNamespace(document=lambda: {}),
    ))
    monkeypatch.setattr(build_toolchain_lock, "_query_marker_environment", lambda *a, **k: {})
    monkeypatch.setattr(
        installer_packaging_seed, "_target",
        lambda python, *a: {"base_prefix": str(python)},
    )
    monkeypatch.setattr(
        local_install_profile, "prepare_registry_lock",
        lambda *a, **k: pytest.fail("foreign tooling target prepared a profile"),
    )
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="selected native target"):
        prepare_manager_application.main([
            "--install-source", str(tmp_path), "--output", str(output),
            "--identity", "{}", "--python", str(tmp_path / "foreign-python"),
            "--uv", str(uv), "--deadline", str(time.monotonic() + 10),
        ])
    assert not output.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows compact build workspace")
@pytest.mark.parametrize("change", ["copy", "retained", "built", "profile-retained", "profile-built"])
def test_suite_refuses_changed_compact_or_retained_source(tmp_path, monkeypatch, change):
    import build_python_artifacts
    import build_toolchain_lock
    import promotion_python_artifacts

    output = Path(local.native_retained_path(tmp_path / "prepared"))
    source = output / "source"
    payload = source / "plugins" / "agent-worktrees"
    payload.mkdir(parents=True)
    (payload / "pyproject.toml").write_text('[project]\nname="agent-worktrees"\nversion="1.0.0"\n')
    (payload / "plugin.json").write_text('{"name":"agent-worktrees","version":"1.0.0"}')
    monkeypatch.setattr(promotion_python_artifacts, "candidate_projects", lambda root: {
        "agent-worktrees": "1.0.0",
    })
    toolchain = SimpleNamespace(record={"fixture": "one-admitted-toolchain"})
    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", lambda *a, **kw: toolchain)
    builder = SimpleNamespace(
        __file__=build_python_artifacts.__file__, uer=build_python_artifacts.uer,
        resolve_vendored_libs=lambda root: [],
    )
    monkeypatch.setattr(local, "_load_module", lambda *a: builder)
    builds = []

    def build(component, *, out_dir, toolchain):
        builds.append(component)
        if not change.startswith("profile-"):
            target = payload if change == "retained" else builder.REPO / "plugins" / component
            (target / "runtime-source.json").write_text("changed")
        return {"plugin": component, "version": "1.0.0", "build_toolchain": toolchain.record}

    builder.build_plugin_artifacts = build
    if change == "copy":
        copytree = shutil.copytree

        def altered_copy(src, dst, *args, **kwargs):
            result = copytree(src, dst, *args, **kwargs)
            if Path(src) == payload:
                (Path(dst) / "runtime-source.json").write_text("changed")
            return result

        monkeypatch.setattr(local.shutil, "copytree", altered_copy)
    record = {
        "schema": local.SCHEMA, "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40, "projects": {},
    }
    message = {
        "copy": "compact build source differs",
        "retained": "retained installation source changed",
        "built": "compact build source changed",
        "profile-retained": "retained installation source changed",
        "profile-built": "compact build source changed",
    }[change]
    with pytest.raises(ValueError, match=message):
        if change.startswith("profile-"):
            with local._built_projects(output, record):
                target = payload if change == "profile-retained" else builder.REPO / "plugins" / "agent-worktrees"
                (target / "runtime-source.json").write_text("changed")
        else:
            local.build_projects(output, record)
    assert builds == ([] if change == "copy" else ["agent-worktrees"])
    assert bool(record["projects"]) == change.startswith("profile-")
    assert (output / local.MANIFEST).exists() == change.startswith("profile-")


@pytest.fixture
def reusable_install_source(tmp_path, monkeypatch, request):
    import build_python_artifacts as builder
    import build_toolchain_lock
    import promotion_python_artifacts as artifacts
    from test_promotion_python_artifacts import _complete_manifest, _wheel_bytes
    from versioned_runtime_source import SOURCE_FILE, SourceIdentity, assembly_hash, runtime_version

    previous = tmp_path / "previous"
    source = previous / "source"
    payload = source / "plugins" / "demo"
    package = payload / "src" / "demo"
    package.mkdir(parents=True)
    body = b"VALUE = 'first-party fixture'\n"
    if getattr(request, "param", False):
        body += b"\ndef main():\n    return 0\n"
    (package / "__init__.py").write_bytes(body)
    (payload / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=68"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="demo"\nversion="1.0.0"\n' +
        ('[project.scripts]\ndemo="demo:main"\n'
         '[project.optional-dependencies]\nbootstrap=["agent-procutil"]\n'
         '[tool.uv.sources]\nagent-procutil={path="../../libs/agent-procutil",editable=true}\n'
         if getattr(request, "param", False) else ""),
        encoding="utf-8",
    )
    if getattr(request, "param", False):
        import local_install_profile as native

        native.source_inputs.engine.freeze_inputs(
            Path(__file__).resolve().parents[1] / "libs" / "agent-procutil", source / "libs",
        )
    (payload / "plugin.json").write_text('{"name":"demo","version":"1.0.0"}\n')
    digest = assembly_hash(payload)
    identity = SourceIdentity(
        "demo", "1.0.0", "a" * 40, "b" * 40, digest,
        runtime_version("1.0.0", "a" * 40, digest),
    )
    (payload / SOURCE_FILE).write_text(json.dumps(identity.document(), sort_keys=True) + "\n")
    wheel_dir = previous / "wheels" / "demo"
    wheel_dir.mkdir(parents=True)
    trusted = promote_release._load_module(Path(builder.__file__), "_local_reuse_test_sources")
    trusted.REPO = source
    trusted.PLUGINS_DIR = source / "plugins"
    trusted.LIBS_DIR = source / "libs"
    directories = [payload, *(directory for _, directory in trusted.resolve_vendored_libs(payload))]
    rows = []
    for directory in directories:
        metadata = tomllib.loads((directory / "pyproject.toml").read_text())["project"]
        name = metadata["name"].replace("-", "_")
        if getattr(request, "param", False):
            from packaging.markers import Marker
            from packaging.requirements import Requirement
            from packaging.version import Version
            from wheel_input_fixture import wheel_bytes

            version = str(Version(metadata["version"]))
            prefix = f"{name}-{version}.dist-info/"
            headers = [
                "Metadata-Version: 2.4", "Name: " + metadata["name"], "Version: " + version,
                *(["Requires-Python: " + metadata["requires-python"]]
                  if "requires-python" in metadata else []),
                *("Requires-Dist: " + value for value in metadata.get("dependencies", [])),
            ]
            for extra, requirements in metadata.get("optional-dependencies", {}).items():
                headers.append("Provides-Extra: " + extra)
                for value in requirements:
                    requirement = Requirement(value)
                    marker = f"({requirement.marker}) and " if requirement.marker is not None else ""
                    requirement.marker = Marker(marker + f"extra == {extra!r}")
                    headers.append("Requires-Dist: " + str(requirement))
            base = directory / "src"
            members = {path.relative_to(base).as_posix(): path.read_bytes() for path in base.rglob("*.py")}
            data = tomllib.loads((directory / "pyproject.toml").read_text())
            for package, patterns in data.get("tool", {}).get("setuptools", {}).get("package-data", {}).items():
                for pattern in patterns:
                    members.update({
                        path.relative_to(base).as_posix(): path.read_bytes()
                        for path in base.joinpath(*package.split(".")).glob(pattern) if path.is_file()
                    })
            if (directory / "LICENSE").is_file():
                members[prefix + "licenses/LICENSE"] = (directory / "LICENSE").read_bytes()
            if metadata.get("scripts"):
                members[prefix + "entry_points.txt"] = (
                    "[console_scripts]\n" +
                    "".join(f"{name} = {target}\n" for name, target in metadata["scripts"].items())
                ).encode()
            module = next(path.split("/")[0] for path in members if path.endswith("/__init__.py"))
            archive = wheel_bytes(
                name=module, prefix=prefix, metadata=("\n".join(headers) + "\n\n").encode(),
                wheel=(
                    "Wheel-Version: 1.0\nGenerator: setuptools (80.9.0)\n"
                    "Root-Is-Purelib: true\nTag: py3-none-any\n"
                ).encode(), extra=members,
            )
        else:
            version = metadata["version"]
            archive = _wheel_bytes(name, version, payload=body)
        wheel = wheel_dir / f"{name}-{version}-py3-none-any.whl"
        wheel.write_bytes(archive)
        rows.append({
            "filename": wheel.name, "role": "plugin" if directory == payload else "vendored-lib",
            "sha256": "sha256:" + hashlib.sha256(archive).hexdigest(),
        })
    manifest = _complete_manifest({
        "schema": "copilot-extensions.python-artifact-manifest", "schema_version": 3,
        "plugin": "demo", "version": "1.0.0",
        "payload_hash": trusted.compute_payload_hash(directories),
        "wheels": rows,
    })
    for row, directory in zip(manifest["wheels"], directories):
        row["source"] = directory.relative_to(source).as_posix()
    record = {
        "schema": local.SCHEMA, "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40,
        "composition": {"main_seed": "c" * 40, "tree": "d" * 40},
        "projects": {"demo": {
            "payload": "source/plugins/demo", "wheels": "wheels/demo",
            "source": identity.document(), "artifact": manifest,
        }},
    }
    (previous / local.MANIFEST).write_text(json.dumps(record))
    output = tmp_path / "next"
    shutil.copytree(source, output / "source")
    current = {**record, "projects": {}}
    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", lambda *a, **kw: pytest.fail(
        "explicit unchanged promoted inputs must not provision another build toolchain",
    ))
    monkeypatch.setattr(builder, "build_wheel", lambda *a, **kw: pytest.fail(
        "explicit unchanged promoted inputs must not execute a first-party build",
    ))
    monkeypatch.setattr(artifacts, "candidate_projects", lambda root: {"demo": "1.0.0"})
    return previous, output, current, record


def test_local_prepared_wheel_reuse_retains_exact_inputs_without_another_build(reusable_install_source):
    previous, output, record, original = reusable_install_source
    result = local.build_projects(output, record, projects=("demo",), reuse_source=previous)
    assert result["projects"] == original["projects"]
    assert (output / "wheels/demo/demo-1.0.0-py3-none-any.whl").read_bytes() == (
        previous / "wheels/demo/demo-1.0.0-py3-none-any.whl"
    ).read_bytes()
    assert json.loads((output / local.MANIFEST).read_text()) == result
    assert not (output / "build-tools").exists()


@pytest.mark.parametrize("reusable_install_source", [True], indirect=True)
@pytest.mark.parametrize("fault", [
    None, "completion", "wheel", "application", "extras", "target", "policy",
    "toolchain", "selected-source",
])
def test_complete_application_reuse_revalidates_outputs_without_constructing(
    reusable_install_source, tmp_path, monkeypatch, fault,
):
    import local_install_profile as native
    from packaging.markers import default_environment
    from packaging.tags import sys_tags
    from runtime_profile import ProfileUnavailable

    previous, output, record, original = reusable_install_source
    completed = tmp_path / "completed"
    shutil.copytree(previous, completed / "inputs")
    selected = native.source_inputs.select_project(completed / "inputs", "demo")
    artifact = selected.project["artifact"]
    archives = {row["filename"]: completed / "inputs" / selected.project["wheels"] / row["filename"]
                for row in artifact["wheels"]}
    expected = {row["filename"]: row["sha256"] for row in artifact["wheels"]}
    deadline = time.monotonic() + 60
    target = {"environment": default_environment(), "tags": [str(tag) for tag in sys_tags()], "device": "cpu"}
    monkeypatch.setattr(native, "_query_marker_environment", lambda *args, **kwargs: target["environment"])
    policy = {"policyId": "fixture-policy"}
    monkeypatch.setattr(native, "_profile_policy", lambda *args: (("fixture", None), policy))
    with native.retain_wheel_inputs(
        archives, expected, max_wheels=256, max_archive_bytes=128 << 20,
        max_total_bytes=512 << 20, max_snapshot_bytes=1 << 30, deadline=deadline,
    ) as retained:
        profile = native.complete_profile(
            selected, target, ["demo==1.0.0"], retained,
            policy_id=policy["policyId"], acquisition_id="fixture-acquisition", deadline=deadline,
        )
        snapshot = native._write_snapshot(
            selected, completed / "profiles" / "demo", profile, retained, deadline=deadline,
        )
    snapshots = {"demo": str(snapshot)}
    with native.source_inputs.project_inputs(snapshot, "demo", deadline=deadline) as (observed, inputs):
        construction = native.source_inputs.application_inputs(observed, inputs)
    application = snapshot / "applications" / "demo"
    application.mkdir(parents=True)
    (application / "application.json").write_text(json.dumps({"construction": construction}))
    (application / "uv.lock").write_text("version = 1\n")
    snapshot_record = native.source_inputs._read(snapshot / local.MANIFEST)
    snapshot_record["projects"]["demo"]["application"] = "applications/demo"
    (snapshot / local.MANIFEST).write_text(json.dumps(snapshot_record))
    completion = local._application_completion(snapshots, deadline=deadline)
    if fault == "completion":
        completion["content"]["demo"] = "0" * 64
    elif fault == "wheel":
        (snapshot / "wheels" / "demo" / next(iter(archives))).write_bytes(b"damaged")
    elif fault == "application":
        (application / "application.json").write_text('{"construction": {}}\n')
    if fault in {"wheel", "application"}:
        # A completion digest is custody evidence, never semantic source approval.
        completion = local._application_completion(snapshots, deadline=deadline)
    (completed / "applications.json").write_text(json.dumps(completion))
    (output / local.MANIFEST).write_text(json.dumps(record))
    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    toolchain_record = json.loads(json.dumps(artifact["build_toolchain"]))
    if fault == "toolchain":
        toolchain_record["lock_id"] = "sha256:" + "0" * 64
    if fault == "target":
        monkeypatch.setattr(native, "_query_marker_environment", lambda *a, **k: {
            **target["environment"], "platform_machine": "different",
        })
    if fault == "policy":
        policy["policyId"] = "changed-policy"
    if fault == "selected-source":
        reuse = local._reuse_projects
        def changed(*args, **kwargs):
            result = reuse(*args, **kwargs)
            (output / "source" / "plugins" / "demo" / "src" / "demo" / "__init__.py").write_text("changed")
            return result
        monkeypatch.setattr(local, "_reuse_projects", changed)
    monkeypatch.setattr(native, "prepare_registry_lock", lambda *a, **k: pytest.fail(
        "complete reuse must not resolve or prepare another native profile",
    ))
    options = dict(
        extras={"demo": ["bootstrap"]} if fault == "extras" else {}, deadline=deadline,
        source_record_sha256=digest, toolchain_record=toolchain_record,
    )
    if fault:
        with pytest.raises(native.ProfileError if fault == "selected-source" else ProfileUnavailable):
            native.reuse_applications(output, completed, ["demo"], **options)
    else:
        assert native.reuse_applications(output, completed, ["demo"], **options) == snapshots
        assert json.loads((completed / "applications.json").read_text()) == completion
        assert native.source_inputs.select_project(snapshot, "demo").identity == selected.identity
    assert not (output / "wheels").exists()
    assert not (output / "build-tools").exists()
    assert record["projects"] == {}
    assert original["projects"]["demo"]["artifact"] == artifact


@pytest.mark.parametrize("phase", [
    "hit", "missing-toolchain", "changed-toolchain", "miss", "worker-failure",
    "timeout", "foreign-map", "late-source",
])
def test_complete_output_reuse_uses_one_provider_without_replaying_worker_failures(
    tmp_path, monkeypatch, phase,
):
    import build_toolchain_lock as toolchains
    import builtins

    repository = tmp_path / "checkout"
    repository.mkdir()
    root = tmp_path / "applications"
    previous, output = root / "previous", root / "incoming"
    toolchain_dir = previous / "inputs" / "build-tools"
    toolchain_dir.mkdir(parents=True)
    record = {
        "schema": local.SCHEMA, "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40,
        "composition": {"main_seed": "c" * 40, "tree": "d" * 40}, "projects": {},
    }
    toolchain = SimpleNamespace(venv_python=tmp_path / "retained-python", record={"fixture": "original"})
    prior = {**record, "projects": {"demo": {"artifact": {"build_toolchain": (
        {"fixture": "changed"} if phase == "changed-toolchain" else toolchain.record
    )}}}}
    (previous / "inputs" / local.MANIFEST).write_text(json.dumps(prior))
    snapshots = {"demo": str(previous / "profiles" / "demo" / "snapshot")}
    (previous / "applications.json").write_text(json.dumps({
        "schema": "copilot-extensions.prepared-applications", "version": 1,
        "snapshots": snapshots, "content": {},
    }))
    calls = []
    deadline = time.monotonic() + 30

    def admit(*args, **kwargs):
        calls.append("admit")
        return ("e" * 40 if phase == "late-source" and calls.count("admit") > 1 else "d" * 40, "b" * 40)

    def compose(_repository, _commit, _main, built, **kwargs):
        calls.append("compose")
        (built / "source").mkdir(parents=True)
        return dict(record)

    def resolve(path, **options):
        assert path == Path(local.native_retained_path(toolchain_dir))
        assert options == {
            "python": getattr(sys, "_base_executable", sys.executable),
            "uv": "selected-uv", "deadline": deadline, "reuse_only": True,
        }
        calls.append("reuse-only")
        if phase == "missing-toolchain":
            raise toolchains.ArtifactBuildError("no complete retained toolchain")
        return toolchain

    def worker(argv, **options):
        calls.append("worker")
        assert argv[0] == sys.executable
        assert argv[argv.index("--reuse-applications") + 1] == str(Path(local.native_retained_path(previous)))
        assert json.loads(argv[argv.index("--toolchain-record") + 1]) == toolchain.record
        assert options["deadline"] == deadline
        assert options["env"] == toolchains.sanitize_subprocess_env()
        if phase == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        if phase in {"miss", "worker-failure"}:
            return subprocess.CompletedProcess(argv, 3 if phase == "miss" else 1, "", "worker refused")
        result = snapshots if phase != "foreign-map" else {"demo": str(tmp_path / "foreign")}
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({name: local.native_retained_path(path) for name, path in result.items()}), "",
        )

    class FreshConstructionSelected(Exception):
        pass

    @contextmanager
    def build(*args, **kwargs):
        calls.append("fresh")
        raise FreshConstructionSelected
        yield

    monkeypatch.setattr(local.preparation, "_admit", admit)
    monkeypatch.setattr(local, "compose", compose)
    monkeypatch.setattr(toolchains, "resolve_toolchain_lock", resolve)
    monkeypatch.setattr(toolchains, "_query_marker_environment", lambda *a, **k: {"fixture": "native"})
    monkeypatch.setattr(local.preparation_process, "run", worker)
    monkeypatch.setattr(local, "_built_projects", build)
    importer = builtins.__import__
    def cold_import(name, *args, **kwargs):
        if name == "local_install_profile" or name.split(".")[0] == "packaging":
            pytest.fail("reuse selection imported Packaging into the cold parent")
        return importer(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", cold_import)
    options = dict(components=("demo",), branch="dev", uv="selected-uv", deadline=deadline)
    if phase == "hit":
        assert local.prepare_applications(repository, "a" * 40, "c" * 40, output, **options) == {
            name: local.native_retained_path(path) for name, path in snapshots.items()
        }
        assert calls == ["admit", "compose", "reuse-only", "worker", "admit"]
    else:
        expected = (
            FreshConstructionSelected if phase in {"missing-toolchain", "changed-toolchain", "miss"}
            else subprocess.TimeoutExpired if phase == "timeout" else ValueError
        )
        with pytest.raises(expected):
            local.prepare_applications(repository, "a" * 40, "c" * 40, output, **options)
        assert calls.count("fresh") == int(phase in {"missing-toolchain", "changed-toolchain", "miss"})
        assert calls.count("worker") <= 1
    assert not (output / "applications.json").exists()
    assert not (output / "wheels").exists()


def test_reused_prepared_wheels_install_in_two_fresh_native_environments(
    reusable_install_source, tmp_path,
):
    uv = shutil.which("uv") or pytest.skip("a real uv executable is required")
    previous, output, record, _ = reusable_install_source
    local.build_projects(output, record, projects=("demo",), reuse_source=previous)
    project = record["projects"]["demo"]
    wheel = output / project["wheels"] / project["artifact"]["wheels"][0]["filename"]
    installed = []
    for name in ("first", "second"):
        root = tmp_path / name
        python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        for command in (
            [sys.executable, "-I", "-m", "venv", "--without-pip", "--copies", str(root)],
            [uv, "pip", "install", "--python", str(python), "--offline",
             "--no-build", "--link-mode", "hardlink", str(wheel)],
        ):
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", "import demo;print(demo.VALUE);print(demo.__file__)"],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        value, path = result.stdout.strip().splitlines()
        assert value == "first-party fixture"
        installed.append(Path(path))
    assert installed[0].read_bytes() == installed[1].read_bytes()
    assert os.path.samefile(*installed)


@pytest.mark.parametrize("reusable_install_source", [True], indirect=True)
@pytest.mark.parametrize("application", [False, True])
def test_reused_prepared_wheels_reach_native_sdk_without_reconstruction(
    reusable_install_source, tmp_path, monkeypatch, application,
):
    import local_install_profile as native
    from packaging.markers import default_environment
    from packaging.tags import sys_tags

    uv = shutil.which("uv") or pytest.skip("a real uv executable is required")
    monkeypatch.setenv("UV_OFFLINE", "1")
    previous, output, record, original = reusable_install_source
    local.build_projects(output, record, projects=("demo",), reuse_source=previous)
    assert record["projects"] == original["projects"]
    source = native.source_inputs
    selected = source.select_project(output, "demo")
    project = selected.project
    archive = output / project["wheels"] / project["artifact"]["wheels"][0]["filename"]
    deadline = time.monotonic() + 30
    with native.retain_wheel_inputs(
        {archive.name: archive},
        {archive.name: project["artifact"]["wheels"][0]["sha256"]},
        max_wheels=1, max_archive_bytes=1 << 20, max_total_bytes=1 << 20,
        max_snapshot_bytes=2 << 20, deadline=deadline,
    ) as retained:
        profile = native.complete_profile(
            selected, {
                "device": "cpu", "environment": default_environment(),
                "tags": [str(tag) for tag in sys_tags()],
            },
            ["demo==1.0.0"], retained, policy_id="fixture-policy", acquisition_id="fixture-acquisition",
            deadline=deadline,
        )
        snapshot = native._write_snapshot(
            selected, output, profile, retained, deadline=deadline, uv=uv if application else None,
        )
    root = tmp_path / "runtime"
    root.mkdir()
    selectors = (root / source.engine.runtime.CURRENT_VERSION_FILE,
                 root / source.engine.runtime.LAST_KNOWN_GOOD_FILE)
    for selector in selectors:
        selector.write_bytes(b"0.9.0\n")
    with source.project_inputs(snapshot, "demo", deadline=deadline) as (observed, inputs):
        assert observed.identity == selected.identity
        selection = source.native_selection(observed, inputs)
        options = {
            "plugin": observed.payload, "uv": Path(uv), "base_python": Path(sys.executable),
            "health_module": "demo", "expected_source_identity": selection["identity"],
            "local_packages": tuple(selection["local_packages"]), "wheel_inputs": inputs,
            "application": snapshot / observed.project["application"] if application else None,
            "wait_timeout": 30, "child_timeout": 30,
        }
        with source.engine.numbered_candidate(root, selection["runtime_version"], **options) as ready:
            assert not ready.reused and ready.payload_hash == selection["payload_hash"]
            python = source.engine.runtime.slot_python(root, ready.slot.name)
            executable = str(source.engine._native_retained_path(python))
            result = subprocess.run(
                [executable, "-I", "-B", "-c", "import demo; assert demo.VALUE == 'first-party fixture'"],
                executable=executable, capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert source.engine.runtime.is_complete(
                root, ready.slot.name, expect_hash=selection["payload_hash"],
            )
            slot = ready.slot
        monkeypatch.setattr(source.engine, "_construct", lambda *a, **kw: pytest.fail(
            "a completed candidate from reused first-party inputs must not reconstruct",
        ))
        with source.engine.numbered_candidate(root, selection["runtime_version"], **options) as ready:
            assert ready.reused and ready.slot == slot
        inputs.verify()
    assert all(selector.read_bytes() == b"0.9.0\n" for selector in selectors)


@pytest.mark.parametrize("changed", [
    "source_commit", "main_head", "composition", "source_descriptor",
    "body", "wheel", "wheel-source", "incompatible", "policy", "missing", "held",
])
def test_local_prepared_reuse_refuses_changed_inputs_without_build_fallback(
    reusable_install_source, monkeypatch, changed,
):
    previous, output, record, original = reusable_install_source
    if changed in ("source_commit", "main_head"):
        record[changed] = "e" * 40
    elif changed == "composition":
        record["composition"] = {**record["composition"], "tree": "e" * 40}
    elif changed == "source_descriptor":
        record["source_descriptor"] = {"kind": "github", "canonical": "github:other/source", "ref": ""}
    elif changed == "body":
        (output / "source/plugins/demo/src/demo/__init__.py").write_text("VALUE = 'changed'\n")
    elif changed == "wheel":
        (previous / "wheels/demo/demo-1.0.0-py3-none-any.whl").write_bytes(b"changed archive")
    elif changed in ("wheel-source", "incompatible"):
        from test_promotion_python_artifacts import _complete_manifest, _wheel_bytes

        manifest = original["projects"]["demo"]["artifact"]
        row = manifest["wheels"][0]
        if changed == "incompatible":
            row["filename"] = "demo-1.0.0-cp27-none-any.whl"
        archive = _wheel_bytes(
            "demo", "1.0.0",
            payload=b"VALUE = 'changed'\n" if changed == "wheel-source" else b"VALUE = 'first-party fixture'\n",
            members={"demo-1.0.0.dist-info/WHEEL": (
                "Wheel-Version: 1.0\nGenerator: setuptools (80.9.0)\nRoot-Is-Purelib: true\n"
                "Tag: cp27-none-any\n" if changed == "incompatible" else
                "Wheel-Version: 1.0\nGenerator: setuptools (80.9.0)\nRoot-Is-Purelib: true\n"
                "Tag: py3-none-any\n"
            ).encode()},
        )
        (previous / "wheels/demo" / row["filename"]).write_bytes(archive)
        row["sha256"] = "sha256:" + hashlib.sha256(archive).hexdigest()
        _complete_manifest(manifest)
        (previous / local.MANIFEST).write_text(json.dumps(original))
    elif changed == "missing":
        original["projects"] = {}
        (previous / local.MANIFEST).write_text(json.dumps(original))
    elif changed == "held":
        import promotion_python_artifacts as artifacts

        validate = artifacts.validate_wheel

        def change_selection(*args, **options):
            validate(*args, **options)
            original["composition"]["tree"] = "e" * 40
            (previous / local.MANIFEST).write_text(json.dumps(original))

        monkeypatch.setattr(artifacts, "validate_wheel", change_selection)
    with pytest.raises((ValueError, RuntimeError), match="source|composition|wheel|artifact|policy|digest"):
        local.build_projects(
            output, record, projects=("demo",), reuse_source=previous,
            source_policy="seasoned-public" if changed == "policy" else "governed",
        )
    assert record["projects"] == {}
    assert not (output / local.MANIFEST).exists()
    assert not (output / "build-tools").exists()


def test_first_party_registry_projection_preserves_selected_extras_and_marker_cycles():
    from local_install_profile import registry_roots

    wheels = {
        "app": {"version": "1", "provides_extras": [], "requires_dist": ["lib[fast]>=1"]},
        "lib": {
            "version": "1", "provides_extras": ["fast"],
            "requires_dist": [
                "app>=1", 'runner[test]>=2 ; extra == "fast"',
                'unused>=1 ; sys_platform == "linux"',
            ],
        },
    }
    assert registry_roots(
        wheels, "app==1", {"sys_platform": "win32"}, deadline=time.monotonic() + 5,
    ) == ["runner[test]>=2"]


def test_first_party_follows_associated_application_wheels_before_registry(tmp_path, monkeypatch):
    import local_install_profile as native

    record = {"projects": {}}
    selections = {}
    for name in ("wrapper", "service"):
        payload = tmp_path / name
        payload.mkdir()
        (payload / "pyproject.toml").write_text(f'[project]\nname="{name}"\nversion="1"\n')
        record["projects"][name] = {"payload": name, "source": {}}
        selections[name] = SimpleNamespace(record=record, payload=payload)
    metadata = {
        "wrapper": {"wrapper": {
            "version": "1", "requires_python": "", "provides_extras": [],
            "requires_dist": ["service>=1"],
        }},
        "service": {"service": {
            "version": "1", "requires_python": "", "provides_extras": [],
            "requires_dist": ["wrapper>=1", "external>=2"],
        }},
    }
    monkeypatch.setattr(
        native, "_associated_first_party",
        lambda snapshot, *, deadline, component: (selections[component], dict(metadata[component])),
    )
    _, wheels = native.first_party(tmp_path, component="wrapper", deadline=time.monotonic() + 5)
    assert set(wheels) == {"wrapper", "service"}
    assert native.registry_roots(
        wheels, "wrapper==1", {}, deadline=time.monotonic() + 5,
    ) == ["external>=2"]


@pytest.fixture
def embedded_profile_source(tmp_path, monkeypatch, request):
    import local_install_profile as native
    from standalone_python_artifacts import injected_resources
    from test_standalone_python_artifacts import _build

    snapshot = tmp_path / "snapshot"
    source, wheels, manifest = _build(
        snapshot / "source", monkeypatch, "worktree-manager", embedded=True,
        materialized=getattr(request, "param", False),
    )
    metadata = source / "pyproject.toml"
    metadata.write_text(
        metadata.read_text().replace('version = "1.0"', 'version = "1.0.0"')
        .replace('name = "worktree_manager"', 'name = "copilot-extensions-worktree-manager"'),
    )
    library = (source if getattr(request, "param", False) else source.parent) / "libs/agent-procutil"
    with (library / "pyproject.toml").open("a") as stream:
        stream.write('\n[tool.setuptools.package-data]\nagent_procutil=["schema.json"]\n')
    (library / "src/agent_procutil/schema.json").write_bytes(b'{"version":1}\n')
    members = {name: path.read_bytes() for name, path in injected_resources(
        "worktree-manager", source.parent,
    ).items()}
    members["worktree_manager/__init__.py"] = (source / "src/worktree_manager/__init__.py").read_bytes()
    _embedded_profile_wheel(wheels, manifest, members)
    sources = native.source_inputs.sources
    digest = sources.assembly_hash(source)
    identity = sources.SourceIdentity(
        "worktree-manager", "1.0.0", "a" * 40, "b" * 40, digest,
        sources.runtime_version("1.0.0", "a" * 40, digest),
    )
    (source / sources.SOURCE_FILE).write_text(json.dumps(identity.document()))
    (snapshot / native.source_inputs.MANIFEST).write_text(json.dumps({
        "schema": native.source_inputs.SCHEMA, "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "projects": {"standalone:worktree-manager": {
            "payload": source.relative_to(snapshot).as_posix(),
            "wheels": wheels.relative_to(snapshot).as_posix(),
            "source": identity.document(), "artifact": manifest,
        }},
    }))
    return snapshot


def _embedded_profile_wheel(directory, manifest, members):
    from build_toolchain_lock import _hash_fields
    from wheel_input_fixture import wheel_bytes

    manifest["version"] = "1.0.0"
    row = manifest["wheels"][0]
    row.update({
        "name": "copilot_extensions_worktree_manager",
        "filename": "copilot_extensions_worktree_manager-1.0.0-py3-none-any.whl",
    })
    archive = directory / row["filename"]
    archive.write_bytes(wheel_bytes(
        name="worktree_manager", prefix="copilot_extensions_worktree_manager-1.0.0.dist-info/",
        metadata=b"Metadata-Version: 2.4\nName: copilot-extensions-worktree-manager\nVersion: 1.0.0\n\n",
        extra=members,
    ))
    row["sha256"] = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest["artifact_id"] = "sha256:" + _hash_fields(
        manifest["payload_hash"], manifest["python_tag"], manifest["abi_tag"],
        manifest["platform_tag"], manifest["build_toolchain"]["lock_id"],
        row["filename"], row["sha256"],
    )


@pytest.mark.parametrize("embedded_profile_source", [False, True], indirect=True)
def test_native_profile_accepts_embedded_libraries_without_separate_distributions(
    embedded_profile_source, tmp_path,
):
    import local_install_profile as native
    from packaging.markers import default_environment
    from packaging.tags import sys_tags

    deadline = time.monotonic() + 30
    selected, wheels = native.first_party(
        embedded_profile_source, deadline=deadline,
    )
    assert set(wheels) == {"copilot-extensions-worktree-manager"}
    assert native.registry_roots(
        wheels, "copilot-extensions-worktree-manager==1.0.0", {}, deadline=time.monotonic() + 5,
    ) == []
    assert len(native.source_inputs.engine._package_roots(selected.payload)) == 2
    archives = {
        row["filename"]: embedded_profile_source / row["wheel_directory"] / row["filename"]
        for row in wheels.values()
    }
    with native.retain_wheel_inputs(
        archives, {row["filename"]: row["sha256"] for row in wheels.values()},
        max_wheels=2, max_archive_bytes=1 << 20, max_total_bytes=2 << 20,
        max_snapshot_bytes=2 << 20, deadline=deadline,
    ) as retained:
        profile = native.complete_profile(
            selected, {
                "device": "cpu", "environment": default_environment(),
                "tags": [str(tag) for tag in sys_tags()],
            },
            ["copilot-extensions-worktree-manager==1.0.0"], retained,
            policy_id="fixture-policy", acquisition_id="fixture-acquisition", deadline=deadline,
        )
        prepared = native._write_snapshot(
            selected, tmp_path / "complete", profile, retained, deadline=deadline,
        )
    with native.source_inputs.project_inputs(prepared, "worktree-manager", deadline=deadline) as (
        observed, inputs,
    ):
        construction = native.source_inputs.native_selection(observed, inputs)
        assert construction["local_packages"] == ["copilot-extensions-worktree-manager"]
        assert len(native.source_inputs.engine._package_roots(observed.payload)) == 2


@pytest.mark.parametrize("member", [
    "agent_procutil/__init__.py", "agent_procutil/LICENSE", "agent_procutil/schema.json",
    "worktree_manager/__init__.py",
])
def test_native_profile_binds_bundled_wheel_members_to_sources(
    embedded_profile_source, member,
):
    import local_install_profile as native
    from promotion_python_artifacts import PromotionArtifactError
    from standalone_python_artifacts import injected_resources

    snapshot = embedded_profile_source
    selected = native.source_inputs.select_project(snapshot, "worktree-manager")
    members = {
        name: path.read_bytes()
        for name, path in injected_resources("worktree-manager", selected.payload.parent).items()
    }
    members["worktree_manager/__init__.py"] = (
        selected.payload / "src/worktree_manager/__init__.py"
    ).read_bytes()
    members[member] = b"substituted bytes\n"
    _embedded_profile_wheel(
        snapshot / selected.project["wheels"], selected.project["artifact"], members,
    )
    (snapshot / native.source_inputs.MANIFEST).write_text(json.dumps(selected.record))
    with pytest.raises(PromotionArtifactError, match="wheel payload differs from candidate source"):
        native.first_party(snapshot, deadline=time.monotonic() + 30)


def test_native_profile_still_requires_unbundled_library_distributions(embedded_profile_source):
    import local_install_profile as native

    snapshot = embedded_profile_source
    selected = native.source_inputs.select_project(snapshot, "worktree-manager")
    metadata = selected.payload / "pyproject.toml"
    metadata.write_text(metadata.read_text().replace(
        'first-party-core = ["agent-procutil"]', 'first-party-core = []',
    ))
    sources = native.source_inputs.sources
    digest = sources.assembly_hash(selected.payload)
    identity = sources.SourceIdentity(
        "worktree-manager", "1.0.0", "a" * 40, "b" * 40, digest,
        sources.runtime_version("1.0.0", "a" * 40, digest),
    )
    (selected.payload / sources.SOURCE_FILE).write_text(json.dumps(identity.document()))
    selected.project["source"] = identity.document()
    (snapshot / native.source_inputs.MANIFEST).write_text(json.dumps(selected.record))
    with pytest.raises(native.ProfileError, match="do not complete"):
        native.first_party(snapshot, deadline=time.monotonic() + 30)


@pytest.mark.parametrize("dependency", ["lib>=2", "lib[unknown]>=1", "external @ https://example.com/x.whl"])
def test_first_party_registry_projection_refuses_invalid_dependencies(dependency):
    from local_install_profile import ProfileError, registry_roots

    wheels = {
        "app": {"version": "1", "provides_extras": [], "requires_dist": [dependency]},
        "lib": {"version": "1", "provides_extras": [], "requires_dist": []},
    }
    with pytest.raises(ProfileError):
        registry_roots(wheels, "app==1", {}, deadline=time.monotonic() + 5)


@pytest.mark.parametrize("extras,requirements", [((), "\n"), (("MCP",), "runtime-support>=2\n")])
def test_native_registry_lock_selects_declared_application_extras(tmp_path, monkeypatch, extras, requirements):
    from contextlib import contextmanager
    import local_install_profile as native

    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pyproject.toml").write_text(
        '[project]\nname="app"\nversion="1.0.0"\n'
        '[project.optional-dependencies]\nmcp=["runtime-support>=2"]\n',
    )
    monkeypatch.setattr(native, "first_party", lambda *a, **kw: (
        SimpleNamespace(payload=payload),
        {"app": {
            "version": "1.0.0", "provides_extras": ["mcp"],
            "requires_dist": ['runtime-support>=2 ; extra == "mcp"'],
        }},
    ))
    monkeypatch.setattr(
        native, "_query_marker_environment",
        lambda *a, **kw: {"sys_platform": "win32", "python_full_version": "3.12.10"},
    )
    compiled = []

    @contextmanager
    def compile_probe(uv, arguments, **kwargs):
        compiled.append(Path(arguments[0]).read_text())
        raise RuntimeError("compiler boundary")
        yield

    monkeypatch.setattr(native.dependencies, "retain_uv_compile", compile_probe)
    with pytest.raises(RuntimeError, match="compiler boundary"):
        native.prepare_registry_lock(
            tmp_path / "input", tmp_path / "profile", uv="unused",
            deadline=time.monotonic() + 5, component="app", extras=extras,
        )
    assert compiled == [requirements]


@pytest.mark.parametrize("extras", [("unknown",), ("mcp", "MCP")])
def test_native_registry_lock_refuses_invalid_extras_before_output(tmp_path, monkeypatch, extras):
    import local_install_profile as native

    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pyproject.toml").write_text(
        '[project]\nname="app"\nversion="1.0.0"\n[project.optional-dependencies]\nmcp=[]\n',
    )
    monkeypatch.setattr(
        native, "first_party", lambda *a, **kw: (SimpleNamespace(payload=payload), {}),
    )
    output = tmp_path / "profile"
    with pytest.raises(ValueError, match="not declared|duplicate"):
        native.prepare_registry_lock(
            tmp_path / "input", output, uv="unused", deadline=time.monotonic() + 5,
            component="app", extras=extras,
        )
    assert not output.exists()


@pytest.mark.parametrize(("selected", "association"), [
    (False, "missing"), (False, "present"), (True, "present"),
    (True, "missing"), (True, "different"), (True, "missing-ssh"), (True, "different-ssh"),
])
def test_native_engine_profile_projects_declared_fleet_ssh_sdk(
    tmp_path, monkeypatch, selected, association,
):
    from contextlib import contextmanager
    import local_install_profile as native
    from packaging.markers import Marker, default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from application_sources import application_sources

    root = Path(__file__).resolve().parent.parent
    payload = root / "plugins/agent-worktrees"
    wheels = {}
    for directory in (payload, root / "fleet-manager", root / "plugins/agent-ssh"):
        metadata = tomllib.loads((directory / "pyproject.toml").read_text())
        project = metadata["project"]
        requirements = list(project.get("dependencies", []))
        extras = project.get("optional-dependencies", {})
        for extra, values in extras.items():
            for value in values:
                requirement = Requirement(value)
                marker = f'extra == "{extra}"'
                if requirement.marker:
                    marker = f"({requirement.marker}) and {marker}"
                requirement.marker = Marker(marker)
                requirements.append(str(requirement))
        wheels[canonicalize_name(project["name"])] = {
            "version": project["version"], "provides_extras": list(extras),
            "requires_dist": requirements,
            "application_sources": application_sources(metadata),
        }
    if association == "missing":
        del wheels["fleet-manager"]
    else:
        wheels["fleet-manager"]["source_project"] = (
            "standalone:fleet-manager"
            if association in {"present", "missing-ssh", "different-ssh"} else "unrelated"
        )
    if association == "missing-ssh":
        del wheels["agent-ssh"]
    else:
        wheels["agent-ssh"]["source_project"] = (
            "unrelated" if association == "different-ssh" else "agent-ssh"
        )
    monkeypatch.setattr(native, "first_party", lambda *a, **kw: (
        SimpleNamespace(payload=payload), wheels,
    ))
    monkeypatch.setattr(native, "_query_marker_environment", lambda *a, **kw: default_environment())
    compiled = []

    @contextmanager
    def compiler(uv, arguments, **kwargs):
        compiled.extend(Path(arguments[0]).read_text().splitlines())
        raise RuntimeError("native compiler boundary")
        yield

    monkeypatch.setattr(native.dependencies, "retain_uv_compile", compiler)
    refused = selected and association != "present"
    expected_error = native.ProfileError if refused else RuntimeError
    expected_message = "selected associated application" if refused else "native compiler boundary"
    with pytest.raises(expected_error, match=expected_message):
        native.main([
            "--install-source", str(tmp_path / "input"), "--output", str(tmp_path / "profile"),
            "--uv", "unused", "--component", "agent-worktrees",
            *(["--extra", "fleet-ssh"] if selected else []),
        ])
    if refused:
        assert compiled == []
        assert not (tmp_path / "profile").exists()
        return
    names = {canonicalize_name(Requirement(value).name) for value in compiled if value}
    assert ("aiohttp" in names) is selected
    assert ("jwcrypto" in names) is selected
    assert not names & {"fleet-manager", "agent-ssh"}


@pytest.mark.parametrize("embedded_profile_source", [False, True], indirect=True)
def test_native_application_dependency_binds_actual_associated_wheel_owner(
    embedded_profile_source,
):
    import local_install_profile as native

    _, wheels = native.first_party(
        embedded_profile_source, deadline=time.monotonic() + 30,
    )
    name = "copilot-extensions-worktree-manager"
    wheels["consumer"] = {
        "version": "1", "provides_extras": [], "requires_dist": [name],
        "application_sources": {name: "standalone:worktree-manager"},
    }
    assert native.registry_roots(
        wheels, "consumer==1", {}, deadline=time.monotonic() + 5,
    ) == []
    wheels["consumer"]["application_sources"][name] = "unrelated"
    with pytest.raises(native.ProfileError, match="selected associated application"):
        native.registry_roots(
            wheels, "consumer==1", {}, deadline=time.monotonic() + 5,
        )


@pytest.mark.parametrize("fault", [None, "missing", "version"])
@pytest.mark.parametrize("composed", [False, True])
@pytest.mark.parametrize("component", [
    "worktree-manager", "agent-worktrees", "index-service", "index-engine",
])
def test_complete_native_profile_inspects_bytes_and_writes_independent_source_snapshot(
    tmp_path, fault, component, composed,
):
    import local_install_profile as native
    from promotion_unit_tests.test_runtime_profile_wheels import _ENVIRONMENT, _encoded

    original = tmp_path / "original"
    payload = original / "source"
    package = payload / "src" / component.replace("-", "_")
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "1.0.0"\n')
    name = (
        "copilot-extensions-worktree-manager" if component == "worktree-manager"
        else "agent-" + component if component.startswith("index-") else component
    )
    (payload / "pyproject.toml").write_text(f'[project]\nname="{name}"\nversion="1.0.0"\n')
    if component not in {"worktree-manager", "index-service", "index-engine"}:
        (payload / "plugin.json").write_text(json.dumps({"name": component, "version": "1.0.0"}))
    sources = native.source_inputs.sources
    digest = sources.assembly_hash(payload)
    identity = sources.SourceIdentity(
        component, "1.0.0", "a" * 40, "b" * 40, digest,
        sources.runtime_version("1.0.0", "a" * 40, digest),
    )
    (payload / sources.SOURCE_FILE).write_text(json.dumps(identity.document()))
    source_record = {
        "schema": native.source_inputs.SCHEMA, "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40,
        "projects": {component: {
            "payload": "source", "source": identity.document(), "artifact": {},
        }},
    }
    if composed:
        source_record.update(
            main_head="c" * 40, composition={"main_seed": "c" * 40, "tree": "d" * 40},
        )
    manifest = original / native.source_inputs.MANIFEST
    manifest.write_text(json.dumps(source_record))
    before = {path.relative_to(original): path.read_bytes() for path in original.rglob("*") if path.is_file()}
    selected = native.source_inputs.select_project(original, component)
    entries = [
        {
            "name": name, "version": "1.0.0",
            "filename": name.replace("-", "_") + "-1.0.0-py3-none-any.whl",
            "requires_python": ">=3.11", "provides_extras": [],
            "requires_dist": ["counter[metrics]>=2" if fault == "version" else "counter[metrics]>=1"],
        },
        {
            "name": "counter", "version": "1.0", "filename": "counter-1.0-py3-none-any.whl",
            "requires_python": "", "provides_extras": ["metrics"],
            "requires_dist": ['absent>=1; sys_platform == "linux"'],
        },
    ]
    wheel_dir = tmp_path / "archives"
    wheel_dir.mkdir()
    archives, expected = {}, {}
    for entry in entries[:1] if fault == "missing" else entries:
        data = _encoded({**entry, "name": entry["name"].upper()})
        path = wheel_dir / entry["filename"]
        path.write_bytes(data)
        archives[path.name] = path
        expected[path.name] = "sha256:" + hashlib.sha256(data).hexdigest()
    deadline = time.monotonic() + 30
    with native.retain_wheel_inputs(
        archives, expected, max_wheels=5, max_archive_bytes=1 << 20,
        max_total_bytes=2 << 20, max_snapshot_bytes=2 << 20, deadline=deadline,
    ) as retained:
        options = {
            "policy_id": "fixture-source-policy", "acquisition_id": "fixture-acquisition",
            "deadline": deadline,
        }
        target = {"device": "cpu", "environment": dict(_ENVIRONMENT), "tags": ["py3-none-any"]}
        if fault:
            with pytest.raises(native.ProfileError, match="closure|version"):
                native.complete_profile(selected, target, [name + "==1.0"], retained, **options)
            return
        profile = native.complete_profile(selected, target, [name + "==1.0"], retained, **options)
        assert next(wheel for wheel in profile.record()["wheels"] if wheel["name"] == "counter")[
            "selected_extras"
        ] == ["metrics"]
        prepared = native._write_snapshot(selected, tmp_path, profile, retained, deadline=deadline)
        destination = "source/worktree-manager" if component == "worktree-manager" else "source"
        assert native.source_inputs.select_project(prepared, component).payload == prepared / destination
        with native.source_inputs.project_inputs(prepared, component, deadline=deadline) as (observed, inputs):
            assert observed.identity == identity
            assert observed.record.get("composition") == source_record.get("composition")
            assert len(inputs.distributions) == 2
            inputs.verify()
    assert {path.relative_to(original): path.read_bytes() for path in original.rglob("*") if path.is_file()} == before
    assert native.source_inputs.select_project(prepared, component).identity == selected.identity


@pytest.mark.parametrize("trusted,indexes", [
    (None, ["primary"]), (("primary", None), ["primary", "foreign"]),
    (("primary", None), ["primary", "primary"]),
])
def test_native_profile_feed_refuses_untrusted_or_supplemental_indexes(monkeypatch, trusted, indexes):
    import local_install_profile as native
    import governed_feed_trust
    import installer_native_feed

    monkeypatch.setattr(governed_feed_trust, "_validated_trusted_index_url", lambda env: trusted)
    monkeypatch.setattr(governed_feed_trust, "_opaque_index_identity", lambda text: "1" * 64)
    monkeypatch.setattr(
        installer_native_feed, "capture_native_feed_inputs",
        lambda *a, **kw: SimpleNamespace(feeds=[
            SimpleNamespace(index_url=url, identity=str(i)) for i, url in enumerate(indexes)
        ]),
    )
    with pytest.raises(native.ProfileError, match="configured|indexes"):
        native._profile_feed(
            SimpleNamespace(identity=SimpleNamespace(document=lambda: {})),
            SimpleNamespace(environment={}), ["original", "retained"],
            uv="uv", download_origins=(), wheels=None, deadline=time.monotonic() + 5,
        )


def test_native_profile_preserves_duplicate_selected_feed_evidence(monkeypatch):
    import local_install_profile as native
    import installer_native_feed

    url = "https://packages.example/simple/"
    feeds = SimpleNamespace(feeds=[
        SimpleNamespace(index_url=url, identity="same-captured-identity", role="default"),
        SimpleNamespace(index_url=url, identity="same-captured-identity", role="configured"),
    ])
    monkeypatch.setattr(
        installer_native_feed, "capture_native_feed_inputs", lambda *a, **kw: feeds,
    )
    _, captured = native._profile_feed(
        SimpleNamespace(identity=SimpleNamespace(document=lambda: {})),
        SimpleNamespace(environment={"UV_DEFAULT_INDEX": url}), ["original", "retained"],
        uv="uv", download_origins=(), wheels=None, deadline=time.monotonic() + 5,
    )
    assert captured is feeds
    assert [item.role for item in captured.feeds] == ["default", "configured"]


@pytest.fixture
def native_source(tmp_path, request):
    return prepare_native_source(tmp_path, request)


def prepare_native_source(tmp_path, request):
    return complete_native_source(tmp_path, prepare_native_source_inputs(tmp_path, request))


def prepare_native_source_inputs(tmp_path, request):
    import local_install_profile as native
    from packaging.markers import default_environment
    from wheel_input_fixture import wheel_bytes

    original = tmp_path / "original"
    payload = original / "source"
    options = getattr(request, "param", False)
    sibling = isinstance(options, dict) and options.get("sibling", False)
    standalone = isinstance(options, dict) and options.get("standalone", False)
    dependency = isinstance(options, dict) and options.get("dependency", False)
    entry = isinstance(options, dict) and options.get("entry", False)
    content = (
        "import json, os, sys\nfrom pathlib import Path\nVALUE = 1\n"
        "def main():\n"
        "    if '--setup' in sys.argv or '--start' in sys.argv:\n"
        "        root = Path(os.environ['DEMO_APP_HOME'])\n"
        "        if '--start' in sys.argv:\n"
        "            assert (root / 'current-version').is_file()\n"
        "            if '--fail-start' in sys.argv:\n"
        "                return 17\n"
        "        (root / ('setup.json' if '--setup' in sys.argv else 'started.json'))"
        ".write_text(json.dumps(sys.argv[1:]))\n"
        "        (root / 'callback-cwd').write_text(str(Path.cwd()))\n"
        "    else:\n"
        "        print('prepared application ready')\n"
        "    return 0\n"
    ) if entry else "VALUE = 1\n"
    package = payload / "src" / "demo_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(content)
    (payload / "pyproject.toml").write_text(
        '[project]\nname="demo-app"\nversion="1.0.0"\n' +
        ('dependencies=["counter>=1"]\n' if dependency else "") +
        '[project.scripts]\ndemo-app="demo_app:main"\n'
        '[project.optional-dependencies]\nbootstrap=["agent-procutil"]\n'
        '[tool.uv.sources]\nagent-procutil={path="' +
        ("../libs/agent-procutil" if sibling else "libs/agent-procutil") + '"}\n',
    )
    callback = {}
    if isinstance(options, dict) and options.get("callback"):
        code = (
            "VALUE = 1\n"
            "import json,pathlib,shutil,sys\n"
            "def prepare_snapshot(data):\n"
            "    slot=pathlib.Path(sys.prefix).parent\n"
            "    source=pathlib.Path(data['frozen_plugin'])\n"
            "    shutil.copytree(source,slot/'callback-payload')\n"
            "    (slot/'callback-source.json').write_text(json.dumps(str(source)))\n"
            "def snapshot_health():\n"
            "    slot=pathlib.Path(sys.prefix).parent\n"
            "    source=pathlib.Path(json.loads((slot/'callback-source.json').read_text()))\n"
            "    assert source.is_dir(), 'source owner ended before health'\n"
            "    assert (slot/'callback-payload'/'pyproject.toml').read_bytes() == "
            "(source/'pyproject.toml').read_bytes()\n"
        )
        (package / "__init__.py").write_text(code, encoding="utf-8")
        callback["demo_app/__init__.py"] = code.encode("utf-8")
    if not (isinstance(options, dict) and options.get("standalone", False)):
        (payload / "plugin.json").write_text('{"name":"demo-app","version":"1.0.0"}\n')
    scripts = payload / "scripts"
    if not standalone or isinstance(options, dict) and options.get("source_install"):
        scripts.mkdir()
    if isinstance(options, dict) and options.get("source_install"):
        (payload / "source-install.json").write_text(json.dumps({
            "schema": "copilot-extensions.native-source-install", "version": 1,
            "windows": {"script": "scripts/install.ps1", "argument": "-InstallSource"},
            "posix": {"script": "scripts/install.sh", "argument": "--install-source"},
        }))
        (scripts / "install.ps1").write_text("# source entry fixture\n")
        (scripts / "install.sh").write_text("# source entry fixture\n")
    root = Path(__file__).resolve().parents[1]
    sdk_owner = options.get("sdk_owner") if isinstance(options, dict) else None
    if sdk_owner is not None:
        shutil.copytree(
            root / "plugins" / sdk_owner / "scripts", scripts,
            dirs_exist_ok=True, symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    native.source_inputs.engine.freeze_inputs(
        root / "libs" / "agent-procutil", (original if sibling else payload) / "libs",
    )
    for name in (
        "versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_source.py",
        "versioned_runtime_construction.py", "versioned_runtime_publication.py",
    ):
        if not standalone:
            shutil.copyfile(root / "libs" / "versioned-runtime" / name, scripts / name)
    if not standalone and (not isinstance(options, dict) or options.get("legacy_bootstrap", True)):
        shutil.copyfile(
            root / "libs" / "installer-engine" / "runtime_launcher.py",
            scripts / "runtime_launcher.py",
        )
    sources = native.source_inputs.sources
    digest = sources.assembly_hash(payload)
    identity = sources.SourceIdentity(
        "demo-app", "1.0.0", "a" * 40, "b" * 40, digest,
        sources.runtime_version("1.0.0", "a" * 40, digest),
    )
    (payload / sources.SOURCE_FILE).write_text(json.dumps(identity.document()))
    (original / native.source_inputs.MANIFEST).write_text(json.dumps({
        "schema": native.source_inputs.SCHEMA, "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "projects": {"demo-app": {"payload": "source", "source": identity.document()}},
    }))
    selected = native.source_inputs.select_project(original, "demo-app")
    archive = tmp_path / "demo_app-1.0.0-py3-none-any.whl"
    archive.write_bytes(wheel_bytes(
        name="demo_app", prefix="demo_app-1.0.0.dist-info/",
        extra={"demo_app/__init__.py": content.encode(), **callback},
        metadata=(
            "Metadata-Version: 2.4\nName: demo-app\nVersion: 1.0.0\n" +
            ("Requires-Dist: counter>=1\n" if dependency else "") +
            (
                "Requires-Dist: absent-marker-fixture; platform_machine != "
                + json.dumps(default_environment()["platform_machine"]) + "\n"
                if isinstance(options, dict) and options.get("native-marker") else ""
            ) + "\n"
        ).encode(),
    ))
    selected.project["artifact"] = {"wheels": [{
        "name": "demo_app", "filename": archive.name,
        "sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
    }]}
    archives = {archive.name: archive}
    expected = {archive.name: "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()}
    if dependency:
        counter = tmp_path / "counter-1.0.0-py3-none-any.whl"
        counter.write_bytes(wheel_bytes(
            name="counter", prefix="counter-1.0.0.dist-info/",
            metadata=b"Metadata-Version: 2.4\nName: counter\nVersion: 1.0.0\n\n",
        ))
        archives[counter.name] = counter
        expected[counter.name] = "sha256:" + hashlib.sha256(counter.read_bytes()).hexdigest()
    return SimpleNamespace(
        native=native, selected=selected, archives=archives, expected=expected, options=options,
    )


def complete_native_source(tmp_path, inputs):
    from packaging.markers import default_environment
    from packaging.tags import sys_tags

    native, selected = inputs.native, inputs.selected
    deadline = time.monotonic() + 120
    with native.retain_wheel_inputs(
        inputs.archives, inputs.expected,
        max_wheels=2, max_archive_bytes=1 << 20, max_total_bytes=2 << 20,
        max_snapshot_bytes=2 << 20, deadline=deadline,
    ) as retained:
        profile = native.complete_profile(
            selected, {
                "device": "cpu", "environment": default_environment(),
                "tags": [str(tag) for tag in sys_tags()],
            },
            ["demo-app==1.0.0"], retained, policy_id="fixture-policy", acquisition_id="fixture-acquisition",
            deadline=deadline,
        )
        output = tmp_path / "prepared"
        output.mkdir()
        uv = None
        if inputs.options:
            uv = shutil.which("uv")
            if uv is None:
                pytest.skip("a real uv executable is required")
        prepared = native._write_snapshot(
            selected, output, profile, retained, deadline=deadline, uv=uv,
        )
    return native.source_inputs, prepared


@pytest.mark.parametrize("native_source", [
    {"sdk_owner": owner} for owner in ("agent-containers", "agent-machines", "agent-mcp")
], indirect=True)
def test_prepared_snapshot_retains_the_enrolled_owner_sdk(native_source, tmp_path):
    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    (tmp_path / "original").rename(tmp_path / "original-unavailable")
    with source.project_inputs(snapshot, "demo-app", deadline=time.monotonic() + 30) as (observed, inputs):
        expected = source.native_selection(observed, inputs)
        result = subprocess.run(
            [
                sys.executable, "-I", "-B", str(selected.payload / "scripts/install_source.py"),
                "--install-source", str(snapshot), "--component", "demo-app",
                "--payload", str(selected.payload), "--native-inputs",
            ],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == expected
    assert not (snapshot / "current-version").exists()


@pytest.mark.parametrize("native_source", [True], indirect=True)
def test_native_profile_emits_independent_locked_uv_application(native_source):
    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    application = snapshot / selected.project["application"]
    assert (application / "uv.lock").is_file()
    assert (application / "wheels" / "demo_app-1.0.0-py3-none-any.whl").is_file()
    assert json.loads((application / "runtime-source.json").read_text()) == selected.identity.document()
    assert all((application / name).is_file() for name in (
        "demo-app.cmd", "demo-app.ps1", "demo-app.sh",
        "install.cmd", "install.ps1", "install.sh", "install.py",
    ))
    assert not (application / ".venv").exists()
    assert not (snapshot / "current-version").exists()
    with source.project_inputs(snapshot, "demo-app") as (_, inputs):
        declared = json.loads((application / "application.json").read_text())["construction"]
        assert declared == source.application_inputs(selected, inputs)


@pytest.mark.parametrize("native_source", [True, {"standalone": True}], indirect=True)
def test_application_candidate_retains_its_admitted_runtime_helpers(native_source, tmp_path):
    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    application = snapshot / selected.project["application"]
    root = tmp_path / "retained-application"
    with source.project_inputs(snapshot, "demo-app") as (_, inputs):
        selection = source.native_selection(selected, inputs)
        with source.engine.numbered_candidate(
            root, selection["runtime_version"], plugin=selected.payload,
            uv=Path(shutil.which("uv")), base_python=Path(sys.executable),
            health_module="demo_app", local_packages=tuple(selection["local_packages"]),
            wheel_inputs=inputs, application=application, process_retention=True,
            expected_source_identity=selection["identity"],
        ) as candidate:
            for name in (
                "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
                "versioned_runtime_source.py", "versioned_runtime_construction.py",
                "versioned_runtime_publication.py",
            ):
                assert (candidate.slot / ".runtime-bootstrap" / name).read_bytes() == (
                    application / "runtime" / name
                ).read_bytes()
            assert source.engine.runtime.is_complete(
                root, candidate.slot.name, expect_hash=selection["payload_hash"],
            )
    assert not (root / "current-version").exists()


@pytest.mark.parametrize("native_source", [True], indirect=True)
def test_application_retention_refuses_missing_helper_without_plugin_fallback(
    native_source, tmp_path,
):
    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    application = snapshot / selected.project["application"]
    assert (selected.payload / "scripts" / "runtime_launcher.py").is_file()
    (application / "runtime" / "runtime_launcher.py").unlink()
    root = tmp_path / "missing-application-helper"
    with source.project_inputs(snapshot, "demo-app") as (_, inputs):
        selection = source.native_selection(selected, inputs)
        with pytest.raises(FileNotFoundError, match="runtime_launcher"):
            with source.engine.numbered_candidate(
                root, selection["runtime_version"], plugin=selected.payload,
                uv=Path(shutil.which("uv")), base_python=Path(sys.executable),
                health_module="demo_app", local_packages=tuple(selection["local_packages"]),
                wheel_inputs=inputs, application=application, process_retention=True,
                expected_source_identity=selection["identity"],
            ):
                pytest.fail("application retained a helper from a different representation")
    assert not source.engine.runtime.marker_path(root, selection["runtime_version"]).exists()
    assert not (root / "current-version").exists()


@pytest.mark.parametrize("native_source", [
    True, pytest.param(
        {"native-marker": True}, id="native-marker-closure",
        marks=pytest.mark.skipif(os.name != "nt", reason="native Windows marker semantics"),
    ),
], indirect=True)
@pytest.mark.parametrize("deep_slot", [False, True])
def test_copied_application_qualifies_after_the_prepared_source_is_removed(
    native_source, tmp_path, deep_slot,
):
    if deep_slot and os.name != "nt":
        pytest.skip("long native CreateProcess cwd control is Windows-specific")
    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    application = snapshot / selected.project["application"]
    record = json.loads((application / "application.json").read_text())
    root = tmp_path / "independent"
    if deep_slot:
        root /= "deep-" + "x" * 72
    slot = root / "versions" / record["construction"]["runtime_version"]
    if deep_slot:
        assert len(str(slot)) > 260
    shutil.copytree(application, slot)
    record.update(setup=[], start=[])
    (slot / "application.json").write_text(json.dumps(record), encoding="utf-8")
    shutil.rmtree(snapshot)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("UV_") and key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    home = tmp_path / "home"
    home.mkdir()
    environment.update(
        UV_PYTHON=sys.executable, UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never",
        HOME=str(home), USERPROFILE=str(home),
    )
    if os.name == "nt":
        environment.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])
        # A copied launcher keeps fixture cleanup independent of mapped UV
        # interpreter links held by other test environments.
        subprocess.run(
            [sys.executable, "-I", "-m", "venv", "--without-pip", "--copies", str(slot / ".venv")],
            env=environment, capture_output=True, text=True, timeout=60, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for executable in (slot / ".venv/Scripts").glob("python*.exe"):
            assert executable.stat().st_nlink == 1
            executable.chmod(executable.stat().st_mode | 0o200)
    command = (
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive",
         "-File", str(slot / "install.ps1")]
        if os.name == "nt" else ["/bin/sh", str(slot / "install.sh")]
    )
    result = subprocess.run(
        command, cwd=tmp_path, env=environment, capture_output=True, text=True,
        timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert source.engine.runtime.is_complete(
        root, slot.name, expect_hash=record["construction"]["payload_hash"],
    )
    assert (root / "current-version").read_text().strip() == slot.name
    assert not (slot / "payload").exists()
    assert not (slot / "source").exists()
    cleanup_deadline = time.monotonic() + 60
    while not source.engine.runtime._rmtree_deferrable(root, label="disposable application fixture"):
        if time.monotonic() >= cleanup_deadline:
            image = slot / ".venv" / "Scripts" / "python.exe"
            facts = image.stat() if image.exists() else None
            pytest.fail(
                f"disposable application cleanup remained blocked: "
                f"attributes={getattr(facts, 'st_file_attributes', None)}, "
                f"links={getattr(facts, 'st_nlink', None)}; "
                f"native output: {result.stdout}{result.stderr}"
            )
        time.sleep(0.25)


@pytest.mark.parametrize("native_source", [{"sibling": True}], indirect=True)
def test_native_profile_preserves_sibling_source_layout(native_source):
    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    packages = source.engine._package_roots(selected.payload)
    assert selected.payload == snapshot / "source"
    assert any(package.is_relative_to(snapshot / "libs") for package in packages)
    assert (snapshot / selected.project["application"] / "uv.lock").is_file()
    with source.project_inputs(snapshot, "demo-app") as (_, inputs):
        assert source.native_selection(selected, inputs)["local_packages"] == ["demo-app"]


@pytest.mark.parametrize("native_source", [True], indirect=True)
@pytest.mark.parametrize("field", ["source_hash", "payload_hash", "local_packages", "wheel_inputs"])
def test_sdk_refuses_application_inputs_that_differ_from_actual_source(
    native_source, tmp_path, field,
):
    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    application = snapshot / selected.project["application"]
    record = json.loads((application / "application.json").read_text())
    if field == "local_packages":
        record["construction"][field].append("unbound-package")
    elif field == "wheel_inputs":
        record["construction"][field]["binding"]["roots"] = ["different-app==1.0.0"]
    else:
        record["construction"][field] = "0" * 64
    (application / "application.json").write_text(json.dumps(record), encoding="utf-8")
    root = tmp_path / "refused"
    with source.project_inputs(snapshot, "demo-app") as (_, inputs):
        selection = source.native_selection(selected, inputs)
        with pytest.raises(ValueError, match="retained actual Source/profile"):
            with source.engine.numbered_candidate(
                root, selection["runtime_version"], plugin=selected.payload,
                uv=Path(shutil.which("uv")), base_python=Path(sys.executable),
                health_module="demo_app", local_packages=tuple(selection["local_packages"]),
                wheel_inputs=inputs, application=application,
                expected_source_identity=selection["identity"],
            ):
                pytest.fail("a mismatched application was admitted")
    slot = root / "versions" / selection["runtime_version"]
    assert not (slot / ".venv").exists()
    assert not source.engine.runtime.marker_path(root, slot.name).exists()
    assert not (root / "current-version").exists()


@pytest.mark.parametrize("native_source", [{"dependency": True}], indirect=True)
def test_native_application_locks_transitive_wheel_sources_offline(native_source):
    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    application = snapshot / selected.project["application"]
    with (application / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    assert project["project"]["dependencies"] == ["demo-app==1.0.0", "counter==1.0.0"]
    assert (application / "uv.lock").is_file()


def test_native_profile_with_only_associated_wheels_does_not_acquire_registry_inputs(
    native_source, tmp_path, monkeypatch,
):
    import installer_wheel_acquisition
    import local_install_profile as native

    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    profile = source._read(snapshot / selected.project["profile"])
    wheels = {row["name"]: row for row in profile["wheels"]}
    monkeypatch.setattr(installer_wheel_acquisition, "acquire_wheel_inputs", lambda *a, **kw: pytest.fail(
        "a first-party-only profile requested external acquisition",
    ))
    monkeypatch.setattr(native, "_profile_feed", lambda *a, **kw: (
        {"policyId": "fixture-policy"}, SimpleNamespace(policy_id="fixture-acquisition"),
    ))
    output = tmp_path / "without-registry"
    output.mkdir()
    prepared = native._prepare_profile(
        snapshot, selected, wheels,
        {"files": [], "target": profile["target"], "roots": profile["roots"]},
        output, None, [], uv=shutil.which("uv") or pytest.skip("a real uv is required"),
        download_origins=(), deadline=time.monotonic() + 120,
    )
    with source.project_inputs(prepared, "demo-app") as (observed, inputs):
        assert observed.identity == selected.identity
        assert len(inputs.distributions) == 1
        inputs.verify()
    assert not (output / "acquisition.json").exists()


def test_associated_application_supplies_the_acquisition_process_owner(native_source, tmp_path, monkeypatch):
    import installer_wheel_acquisition
    import local_install_profile as native

    source, snapshot = native_source
    application = source.select_project(snapshot, "demo-app")
    owner = next(
        package for package in source.engine._package_roots(application.payload)
        if source.engine._package_name(package) == "agent-procutil"
    )
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    (wrapper / "pyproject.toml").write_text('[project]\nname="wrapper"\nversion="1.0"\n')
    selected = SimpleNamespace(
        payload=wrapper, record=application.record,
        identity=SimpleNamespace(assembly_hash="a" * 64),
    )
    monkeypatch.setattr(native, "_profile_feed", lambda *a, **kw: (
        {"policyId": "fixture-policy"},
        SimpleNamespace(feeds=(), download_origins=(), cutoffs={}, policy_id="fixture-acquisition",
                        context=None, proxies={}),
    ))

    class OwnerObserved(Exception):
        pass

    def acquire(_files, **options):
        assert Path(options["procutil"].__file__) == owner / "src" / "agent_procutil" / "__init__.py"
        raise OwnerObserved

    monkeypatch.setattr(installer_wheel_acquisition, "acquire_wheel_inputs", acquire)
    with pytest.raises(OwnerObserved):
        native._prepare_profile(
            snapshot, selected, {"demo-app": {"wheel_directory": application.project["wheels"]}},
            {"files": [{"fixture": "selected-external-wheel"}]}, tmp_path, None, [],
            uv="unused", download_origins=(), deadline=time.monotonic() + 30,
        )


def test_native_profile_selection_refuses_unassociated_wheels(native_source):
    source, snapshot = native_source
    with source.project_inputs(snapshot, "demo-app") as (selected, inputs):
        selection = source.native_selection(selected, inputs)
        assert selection["runtime_version"] == selected.identity.runtime_version + (
            ".profile." + inputs.receipt()["lock_id"][7:23]
        )
        assert selection["local_packages"] == ["demo-app"]
        assert selection["payload_hash"] == source.engine._wheel_construction_hash(
            source.engine._construction_hash(
                source.engine._source_files(source.engine._package_roots(selected.payload)), (), ("demo-app",),
            ),
            inputs,
        )
        selected.project["artifact"]["wheels"][0]["sha256"] = "sha256:" + "0" * 64
        with pytest.raises(ValueError, match="differs from its source association"):
            source.native_selection(selected, inputs)


@pytest.mark.parametrize("failure", [
    None, "profile", "construction", "source", "late", "target", "record", "record-late", "worker-map",
])
def test_complete_application_preparation_uses_existing_owners(
    native_source, tmp_path, monkeypatch, failure,
):
    import local_install_profile as native
    import build_toolchain_lock
    import builtins

    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    selected.project["application"] = "applications/demo-app"
    application = snapshot / selected.project["application"]
    application.mkdir(parents=True)
    with source.project_inputs(snapshot, "demo-app") as (observed, inputs):
        construction = source.application_inputs(observed, inputs)
    (application / "application.json").write_text(json.dumps({"construction": construction}))
    (application / "uv.lock").write_text("version = 1\n")
    (snapshot / source.MANIFEST).write_text(json.dumps(selected.record))
    repo = tmp_path / "checkout"
    repo.mkdir()
    output = tmp_path / "applications"
    deadline = time.monotonic() + 30
    calls = []

    def admit(repository, commit, branch, **options):
        assert repository == repo and commit == "a" * 40 and branch == "dev"
        assert options == {"deadline": deadline}
        calls.append("admit")
        return ("c" * 40 if failure == "late" and calls.count("admit") > 1 else "d" * 40, "b" * 40)

    def compose(repository, commit, main, destination, **options):
        assert repository == repo and commit == "a" * 40 and main == "c" * 40
        assert options == {
            "deadline": deadline, "finalized_main": False, "source_descriptor": None,
        }
        calls.append("compose")
        shutil.copytree(snapshot, destination)
        return json.loads(json.dumps(selected.record))

    held = []

    @contextmanager
    def build(destination, record, **options):
        assert destination.name == "inputs"
        assert options == {
            "projects": ("demo-app",), "uv": "selected-uv", "deadline": deadline,
            "retain_toolchain": True,
        }
        assert record == selected.record
        calls.append("build")
        held.append(True)
        try:
            yield record, SimpleNamespace(venv_python=tmp_path / "borrowed-python")
        finally:
            held.clear()
            calls.append("release")

    def profile(built, destination, **options):
        assert built.name == "inputs"
        assert options == {
            "uv": "selected-uv", "deadline": deadline, "prepare_profile": True,
            "component": "demo-app", "extras": ("selected-extra",),
        }
        calls.append("profile")
        assert held == [True]
        retained = destination / "snapshot"
        shutil.copytree(snapshot, retained)
        if failure == "construction":
            (retained / selected.project["application"] / "application.json").write_text(
                '{"construction": {}}\n',
            )
        if failure == "record-late":
            record_path = built / source.MANIFEST
            altered = json.loads(record_path.read_text())
            altered["main_head"] = "e" * 40
            record_path.write_text(json.dumps(altered))
        return {
            "phase": "registry-locked" if failure == "profile" else "profile-prepared",
            "install_ready": failure != "profile", "snapshot": str(retained),
            "source": {} if failure == "source" else selected.identity.document(),
        }

    def query(python, **options):
        assert held == [True]
        assert options == {"deadline": deadline}
        return {"python_full_version": "3.11.0", "sys_platform": (
            "foreign" if failure == "target" and calls[-1] == "worker" else "native"
        )}

    def worker(argv, **options):
        assert held == [True]
        assert argv[:2] == [
            sys.executable, str(Path(native.__file__)),
        ]
        assert options["env"] == build_toolchain_lock.sanitize_subprocess_env()
        assert options["deadline"] == deadline
        calls.append("worker")
        if failure == "record":
            record_path = Path(argv[argv.index("--install-source") + 1]) / source.MANIFEST
            altered = json.loads(record_path.read_text())
            altered["source_commit"] = "e" * 40
            record_path.write_text(json.dumps(altered))
        captured = io.StringIO()
        try:
            from contextlib import redirect_stdout
            with redirect_stdout(captured):
                assert native.main(argv[2:]) == 0
        except ValueError as error:
            return subprocess.CompletedProcess(argv, 1, "", str(error))
        return subprocess.CompletedProcess(
            argv, 0, "{}" if failure == "worker-map" else captured.getvalue(), "",
        )

    monkeypatch.setattr(local.preparation, "_admit", admit)
    monkeypatch.setattr(local, "compose", compose)
    monkeypatch.setattr(local, "_built_projects", build)
    monkeypatch.setattr(native, "prepare_registry_lock", profile)
    monkeypatch.setattr(build_toolchain_lock, "_query_marker_environment", query)
    monkeypatch.setattr(native, "_query_marker_environment", query)
    in_worker = []
    importer = builtins.__import__

    def cold_import(name, *args, **kwargs):
        if not in_worker and (name == "local_install_profile" or name.split(".")[0] == "packaging"):
            pytest.fail("cold preparation borrowed an incumbent Packaging dependency")
        return importer(name, *args, **kwargs)

    def isolated_worker(*args, **kwargs):
        in_worker.append(True)
        try:
            return worker(*args, **kwargs)
        finally:
            in_worker.clear()

    monkeypatch.setattr(local.preparation_process, "run", isolated_worker)
    monkeypatch.setattr(builtins, "__import__", cold_import)
    options = dict(
        components=("demo-app",), branch="dev", uv="selected-uv", deadline=deadline,
        extras={"demo-app": ("selected-extra",)},
    )
    if failure:
        message = {
            "profile": "did not complete", "construction": "matching locked construction",
            "source": "differs from the selected source", "late": "checkout changed",
            "target": "differs from the selected native target",
            "record": "record differs from the selected source",
            "record-late": "record differs from the selected source",
            "worker-map": "another selected snapshot",
        }[failure]
        with pytest.raises(ValueError, match=message):
            local.prepare_applications(repo, "a" * 40, "c" * 40, output, **options)
        monkeypatch.setattr(builtins, "__import__", importer)
        assert not (output / "applications.json").exists()
    else:
        result = local.prepare_applications(repo, "a" * 40, "c" * 40, output, **options)
        monkeypatch.setattr(builtins, "__import__", importer)
        assert result == {"demo-app": str(Path(local.native_retained_path(
            output / "profiles" / "demo-app" / "snapshot",
        )))}
        completion = json.loads((output / "applications.json").read_text())
        assert completion == local._application_completion(result, deadline=deadline)
        assert completion["snapshots"] == result
        assert calls == ["admit", "compose", "build", "worker", "profile", "release", "admit"]
        with source.project_inputs(Path(result["demo-app"]), "demo-app") as (observed, inputs):
            assert observed.identity == selected.identity
            inputs.verify()
    assert not held


@pytest.mark.parametrize("case", [
    "empty", "duplicate", "unsafe", "extras", "extra-name", "extra-duplicate", "extra-type",
    "inside", "expired",
])
def test_complete_application_preparation_refuses_before_source_effects(tmp_path, monkeypatch, case):
    repo = tmp_path / "checkout"
    repo.mkdir()
    monkeypatch.setattr(local.preparation, "_admit", lambda *args, **kwargs: pytest.fail(
        "invalid application options reached source admission",
    ))
    options = dict(
        components=("demo-app",), extras=None, branch="dev", uv="uv",
        deadline=time.monotonic() + 30,
    )
    output = tmp_path / "output"
    if case == "empty":
        options["components"] = ()
    elif case == "duplicate":
        options["components"] *= 2
    elif case == "unsafe":
        options["components"] = ("../escape",)
    elif case == "extras":
        options["extras"] = {"unselected": ()}
    elif case.startswith("extra-"):
        options["extras"] = {"demo-app": {
            "extra-name": ("../escape",), "extra-duplicate": ("same", "same"), "extra-type": ([],),
        }[case]}
    elif case == "inside":
        output = repo / "output"
    else:
        options["deadline"] = 0
    with pytest.raises((ValueError, local.preparation.SourcePreparationError)):
        local.prepare_applications(repo, "a" * 40, "b" * 40, output, **options)
    assert not output.exists()


def test_single_profile_cli_keeps_selected_component_and_budget(tmp_path, monkeypatch, capsys):
    import local_install_profile as native

    calls = []

    def profile(snapshot, output, **options):
        calls.append((snapshot, output, options))
        return {"phase": "registry-locked", "install_ready": False, "files": []}

    monkeypatch.setattr(native, "prepare_registry_lock", profile)
    assert native.main([
        "--install-source", str(tmp_path / "source"), "--output", str(tmp_path / "output"),
        "--uv", "selected-uv", "--deadline", "123", "--component", "demo-app",
        "--extra", "one",
    ]) == 0
    assert calls == [(tmp_path / "source", tmp_path / "output", {
        "uv": "selected-uv", "deadline": 123.0, "prepare_profile": False,
        "download_origins": (), "component": "demo-app", "extras": ("one",),
    })]
    assert json.loads(capsys.readouterr().out)["install_ready"] is False


@pytest.mark.parametrize("phase", ["hit", "miss", "target", "selected-source"])
def test_complete_reuse_cli_distinguishes_cache_misses_from_selected_input_failure(
    tmp_path, monkeypatch, capsys, phase,
):
    import local_install_profile as native
    from runtime_profile import ProfileUnavailable

    calls = []
    target = {"fixture": "selected"}
    monkeypatch.setattr(native, "_query_marker_environment", lambda *a, **k: (
        {"fixture": "foreign"} if phase == "target" else target
    ))
    def reuse(snapshot, previous, components, **options):
        calls.append((snapshot, previous, components, options))
        if phase == "miss":
            raise ProfileUnavailable("prior output is unavailable")
        if phase == "selected-source":
            raise native.ProfileError("selected source changed")
        return {"demo": str(previous / "profiles" / "demo" / "snapshot")}
    monkeypatch.setattr(native, "reuse_applications", reuse)
    monkeypatch.setattr(native, "prepare_applications", lambda *a, **k: pytest.fail(
        "the reuse worker must never select fresh construction",
    ))
    args = [
        "--install-source", str(tmp_path / "source"), "--output", str(tmp_path / "unused"),
        "--uv", "unused-uv", "--deadline", str(time.monotonic() + 30),
        "--application-components", '["demo"]', "--target-environment", json.dumps(target),
        "--source-record-sha256", "a" * 64, "--reuse-applications", str(tmp_path / "completed"),
        "--toolchain-record", '{"fixture": "original"}',
    ]
    if phase == "selected-source":
        with pytest.raises(native.ProfileError, match="selected source changed"):
            native.main(args)
    else:
        assert native.main(args) == (0 if phase == "hit" else 3)
    captured = capsys.readouterr()
    if phase == "hit":
        assert json.loads(captured.out) == {
            "demo": str(tmp_path / "completed" / "profiles" / "demo" / "snapshot"),
        }
    else:
        assert captured.out == ""
    assert len(calls) == int(phase != "target")
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("components,extras", [
    ([], {}), (["same", "same"], {}), (["../escape"], {}), (["demo-app"], []),
    (["demo-app"], {"other": []}), (["demo-app"], {"demo-app": ["../escape"]}),
    (["demo-app"], {"demo-app": ["same", "same"]}),
])
def test_profile_batch_refuses_invalid_selection_before_reading_source(
    tmp_path, monkeypatch, components, extras,
):
    import local_install_profile as native

    monkeypatch.setattr(native.source_inputs, "_read", lambda *args: pytest.fail(
        "invalid batch selection reached source inspection",
    ))
    with pytest.raises(ValueError, match="bounded unique"):
        native.prepare_applications(
            tmp_path / "source", tmp_path / "output", components, extras=extras,
            uv="unused", deadline=time.monotonic() + 30, source_record_sha256="a" * 64,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("repair", [False, True])
def test_application_cli_forwards_selection_and_callback_arguments(tmp_path, monkeypatch, capsys, repair):
    import local_install_profile
    source = local_install_profile.source_inputs
    snapshot, payload, root, uv, python = (
        tmp_path / name for name in ("snapshot", "payload", "runtime", "uv", "python")
    )
    selected = SimpleNamespace(identity=SimpleNamespace(package_version="1.0"))
    monkeypatch.setattr(source, "select_project", lambda *args: selected)
    monkeypatch.setattr(source.sources, "read_source_identity", lambda *args, **kwargs: selected.identity)
    calls = []
    def install(*args, **kwargs):
        calls.append((args, kwargs))
        return {"state": "installed", "slot": str(root / "versions" / "selected")}
    monkeypatch.setattr(source, "install_application", install)
    arguments = [
        "--install-source", str(snapshot), "--component", "demo-app",
        "--payload", str(payload), "--root", str(root), "--uv", str(uv),
        "--base-python", str(python), "--health-module", "demo_app",
        "--expected-runtime-version", "selected", "--expected-payload-hash", "a" * 64,
        "--install-application", *(["--repair"] if repair else []),
        "--", "--root", "callback root", "--repair", "--",
    ]
    assert source.main(arguments) == 0
    assert calls == [((snapshot, "demo-app", payload, root), {
        "uv": uv, "base_python": python, "health_module": "demo_app",
        "expected_version": "selected", "expected_hash": "a" * 64,
        "arguments": ("--root", "callback root", "--repair", "--"), "repair": repair,
    })]
    assert json.loads(capsys.readouterr().out) == {
        "state": "installed", "slot": str(root / "versions" / "selected"),
    }


@pytest.mark.parametrize("arguments", [
    ["--install-application"],
    ["--install-application", "--native-session"],
    ["--repair"],
    ["--", "--callback"],
    ["--install-application", "unseparated"],
])
def test_application_cli_refuses_invalid_modes_before_source_access(monkeypatch, arguments):
    import local_install_profile
    source = local_install_profile.source_inputs
    monkeypatch.setattr(source, "select_project", lambda *args: pytest.fail(
        "invalid CLI options reached source selection",
    ))
    with pytest.raises(SystemExit) as error:
        source.main(["--install-source", "unused", "--component", "demo-app", *arguments])
    assert error.value.code == 2


@pytest.fixture
def application_adapter(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_application_adapter_source",
        Path(local.__file__).resolve().parents[1] / "libs" / "installer-engine" / "install_source.py",
    )
    assert spec is not None and spec.loader is not None
    source = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, source)
    spec.loader.exec_module(source)
    snapshot, payload, root = (tmp_path / name for name in ("snapshot", "payload", "runtime"))
    application = snapshot / "application"
    application.mkdir(parents=True)
    declaration = {
        "name": "demo-app", "construction": {"fixture": True},
        "module": "demo_app", "commands": ["demo-app"],
        "root_environment": "DEMO_APP_HOME", "default_directory": "demo-app",
        "setup": ["--setup"], "start": ["--start"],
    }
    (application / "application.json").write_text(json.dumps(declaration))
    version = "1.0.profile.fixture"
    old = root / "versions" / version
    old.mkdir(parents=True)
    (old / "application.json").write_text(json.dumps(declaration))
    (old / "preserved").write_bytes(b"incumbent bytes")
    selected = SimpleNamespace(
        payload=payload, identity=SimpleNamespace(package_version="1.0"),
        project={"application": "application"},
    )
    inputs = SimpleNamespace(verify=lambda: None)
    selection = {
        "runtime_version": version, "payload_hash": "a" * 64,
        "extras": [], "local_packages": ["demo-app"], "identity": {"fixture": True},
    }
    fixture = SimpleNamespace(
        root=root, old=old, application=application, inputs=inputs, versions=[],
        failures=[], children=[], child_error=None, exit_error=None, source=source,
        selection=selection, candidate_options=[],
    )

    @contextmanager
    def project_inputs(*_):
        yield selected, inputs

    @contextmanager
    def candidate(home, generation, **options):
        fixture.versions.append(generation)
        fixture.candidate_options.append(options)
        if fixture.failures:
            raise fixture.failures.pop(0)
        slot = home / "versions" / generation
        reused = slot.exists()
        if not slot.exists():
            slot.mkdir(parents=True)
            (slot / "application.json").write_bytes((application / "application.json").read_bytes())
        options["validate_candidate"](slot)
        yield SimpleNamespace(
            slot=slot, payload_hash=selection["payload_hash"],
            frozen_plugin=payload, descriptor=7, reused=reused,
        )
        if fixture.exit_error:
            raise fixture.exit_error

    def child(_argv, **options):
        slot = options["candidate"]
        fixture.children.append(slot.name)
        if fixture.child_error:
            raise fixture.child_error
        (slot / "install-success.json").write_text(json.dumps({
            "state": "installed", "version": slot.name,
        }))

    monkeypatch.setattr(source, "project_inputs", project_inputs)
    monkeypatch.setattr(source, "native_selection", lambda *_: selection)
    monkeypatch.setattr(source, "application_inputs", lambda *_: declaration["construction"])
    monkeypatch.setattr(source.sources, "read_source_identity", lambda *_a, **_kw: selected.identity)
    monkeypatch.setattr(source.engine, "numbered_candidate", candidate)
    monkeypatch.setattr(source.engine, "_package_roots", lambda *_: ())
    monkeypatch.setattr(source.engine, "_child", child)
    fixture.install = lambda component="demo-app", **options: source.install_application(
        snapshot, component, payload, fixture.root, uv=tmp_path / "uv",
        base_python=Path(sys.executable), health_module="demo_app",
        expected_version=version, expected_hash=selection["payload_hash"], **options,
    )
    return fixture


@pytest.mark.parametrize("repair,arguments", [
    (False, ()), (False, ("--config", "fixture")), (True, ()),
])
def test_application_adapter_callback_paths_reenter_owner_on_reuse_or_repair(
    application_adapter, repair, arguments,
):
    fixture = application_adapter
    initial = fixture.install()
    result = fixture.install(repair=repair, arguments=arguments)
    selected = Path(result["slot"])
    assert fixture.children == [Path(initial["slot"]).name, selected.name]
    assert fixture.versions == fixture.children
    if repair:
        assert selected != fixture.old and selected.name.startswith(fixture.old.name + ".repair.")
    else:
        assert result == initial and selected == fixture.old
    assert (fixture.old / "preserved").read_bytes() == b"incumbent bytes"
    assert json.loads((selected / "install-success.json").read_text()) == {
        "state": "installed", "version": selected.name,
    }


@pytest.mark.parametrize("component", ["demo-app", "agent-worktrees"])
def test_application_adapter_prepares_worktrees_before_engine_completion(
    application_adapter, component,
):
    fixture = application_adapter
    identity = {"package_version": "1.0", "source_commit": "a" * 40, "plugin": component}
    fixture.selection["identity"]["runtime_source"] = identity
    fixture.install(component=component)
    options, = fixture.candidate_options
    if component == "agent-worktrees":
        assert options["health_module"] == "agent_worktrees.runtime_candidate"
        assert options["health_callable"] == "verify_runtime_candidate"
        assert options["construction_callable"] == (
            "agent_worktrees.runtime_candidate:prepare_runtime_candidate"
        )
        assert options["construction_data"] == {
            "version": "1.0", "commit": identity["source_commit"], "branch": "source",
            "source": "source:" + identity["source_commit"],
            "deployment_source": json.dumps(identity),
        }
    else:
        assert options["health_module"] == "demo_app"
        assert "health_callable" not in options and "construction_callable" not in options


def test_application_adapter_requires_explicit_repair_for_changed_callbacks(application_adapter):
    fixture = application_adapter
    before = (fixture.old / "application.json").read_bytes()
    declaration = json.loads((fixture.application / "application.json").read_text())
    declaration["setup"].append("--changed")
    (fixture.application / "application.json").write_text(json.dumps(declaration))
    with pytest.raises(fixture.source.engine.ContentRepairRequired, match="distinct repair generation"):
        fixture.install()
    assert fixture.versions == [fixture.old.name]
    assert not fixture.children
    assert (fixture.old / "application.json").read_bytes() == before
    assert (fixture.old / "preserved").read_bytes() == b"incumbent bytes"
    repaired = Path(fixture.install(repair=True)["slot"])
    assert repaired != fixture.old and fixture.children == [repaired.name]
    assert json.loads((repaired / "application.json").read_text()) == declaration


@pytest.mark.parametrize("damage", ["health", "content", "missing", "malformed", "different"])
def test_application_adapter_automatically_repairs_only_preexecution_incumbent_damage(
    application_adapter, damage,
):
    fixture = application_adapter
    engine = fixture.source.engine
    if damage in {"health", "content"}:
        failure = engine.HealthRepairRequired if damage == "health" else engine.ContentRepairRequired
        fixture.failures.append(failure("damaged incumbent"))
    else:
        path = fixture.old / "application.json"
        if damage == "missing":
            path.unlink()
        else:
            path.write_text("invalid json" if damage == "malformed" else '{"different":true}')
    result = fixture.install()
    repaired = Path(result["slot"])
    assert len(fixture.versions) == 2
    assert fixture.versions[0] == fixture.old.name
    assert repaired != fixture.old and repaired.name.startswith(fixture.old.name + ".repair.")
    assert fixture.children == [repaired.name]
    assert (fixture.old / "preserved").read_bytes() == b"incumbent bytes"
    assert not (fixture.old / "install-success.json").exists()
    (fixture.root / "current-version").write_text(repaired.name + "\n")
    assert fixture.install() == result
    assert fixture.versions[-1] == repaired.name and len(fixture.versions) == 3


@pytest.mark.parametrize("boundary", [
    "fresh", "explicit", "second", "incoming", "incoming-declaration",
    "permission", "deadline", "startup", "retention",
])
def test_application_adapter_never_replays_other_failures_or_unknown_application_effects(
    application_adapter, tmp_path, boundary,
):
    fixture = application_adapter
    failure = fixture.source.engine.HealthRepairRequired("fixture failure")
    options = {}
    if boundary == "fresh":
        fixture.root = tmp_path / "fresh"
    elif boundary == "explicit":
        options["repair"] = True
    elif boundary == "permission":
        failure = PermissionError("fixture permission")
    elif boundary == "deadline":
        failure = TimeoutError("fixture deadline")
    if boundary == "startup":
        fixture.child_error = failure
    elif boundary == "retention":
        fixture.exit_error = failure
    else:
        fixture.failures.append(failure)
        if boundary == "second":
            fixture.failures.append(failure)
        elif boundary == "incoming":
            def changed():
                raise ValueError("incoming inputs changed")
            fixture.inputs.verify = changed
        elif boundary == "incoming-declaration":
            fixture.inputs.verify = lambda: (fixture.application / "application.json").write_text("{}")
    expected = ValueError if boundary.startswith("incoming") else type(failure)
    with pytest.raises(expected) as observed:
        fixture.install(**options)
    if not boundary.startswith("incoming"):
        assert observed.value is failure
    assert len(fixture.versions) == (2 if boundary == "second" else 1)
    assert len(fixture.children) == (1 if boundary in {"startup", "retention"} else 0)
    assert (fixture.old / "preserved").read_bytes() == b"incumbent bytes"


@pytest.mark.parametrize("selector", [b"\xff", b"../outside\n"])
def test_application_adapter_does_not_require_readable_incumbent_selection(
    application_adapter, selector,
):
    fixture = application_adapter
    (fixture.root / "current-version").write_bytes(selector)
    assert Path(fixture.install()["slot"]) == fixture.old
    assert fixture.versions == [fixture.old.name]
    assert fixture.children == [fixture.old.name]


def test_native_candidate_rejects_changed_selection_before_constructing(native_source, tmp_path, monkeypatch):
    source, snapshot = native_source
    selected = source.select_project(snapshot, "demo-app")
    monkeypatch.setattr(source.engine, "numbered_candidate", lambda *a, **kw: pytest.fail(
        "changed source/profile input reached construction",
    ))
    with pytest.raises(ValueError, match="changed before construction"):
        source.native_session(
            snapshot, "demo-app", selected.payload, tmp_path / "runtime",
            uv=Path("unused"), base_python=Path(sys.executable), health_module="demo_app",
            expected_version=selected.identity.runtime_version, expected_hash="0" * 64,
        )
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("changed", ["selection", "application", "declaration", "manager"])
@pytest.mark.parametrize("native_source", [True], indirect=True)
def test_application_adapter_refuses_incomplete_or_changed_inputs_before_construction(
    native_source, tmp_path, monkeypatch, changed,
):
    source, snapshot = native_source
    with source.project_inputs(snapshot, "demo-app") as (selected, inputs):
        selection = source.native_selection(selected, inputs)
    if changed == "selection":
        selection["payload_hash"] = "0" * 64
    elif changed == "application":
        path = snapshot / source.MANIFEST
        record = json.loads(path.read_text())
        del record["projects"]["demo-app"]["application"]
        path.write_text(json.dumps(record))
    else:
        path = snapshot / selected.project["application"] / "application.json"
        record = json.loads(path.read_text())
        if changed == "declaration":
            record["construction"]["source_hash"] = "0" * 64
        else:
            record["name"] = "worktree-manager"
        path.write_text(json.dumps(record))
    monkeypatch.setattr(source.engine, "numbered_candidate", lambda *a, **kw: pytest.fail(
        "unsupported application inputs reached construction",
    ))
    with pytest.raises(ValueError, match="changed|prepared application|declaration|native lifecycle"):
        source.install_application(
            snapshot, "demo-app", selected.payload, tmp_path / "runtime",
            uv=Path(shutil.which("uv")), base_python=Path(sys.executable),
            health_module="demo_app", expected_version=selection["runtime_version"],
            expected_hash=selection["payload_hash"],
        )
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize(
    "native_source", [
        {"entry": True}, {"entry": True, "standalone": True},
        pytest.param({"entry": True, "legacy_bootstrap": False}, id="no-legacy-bootstrap"),
    ], indirect=True,
)
def test_application_adapter_runs_real_install_repeat_and_retains_failed_start(
    native_source, tmp_path, tmp_path_factory, monkeypatch,
):
    source, snapshot = native_source
    run_owned = source.engine.run_owned
    activation_calls = []

    def observed_child(argv, **options):
        activation = any(Path(argument).name == "install.py" for argument in argv)
        assert options.get("allow_breakaway", False) is activation
        if activation:
            activation_calls.append(tuple(argv))
        return run_owned(argv, **options)

    monkeypatch.setattr(source.engine, "run_owned", observed_child)
    with source.project_inputs(snapshot, "demo-app") as (selected, inputs):
        selection = source.native_selection(selected, inputs)
    application = snapshot / selected.project["application"]
    path = application / "application.json"
    record = json.loads(path.read_text())
    record.update(setup=["--setup"], start=["--start"])
    path.write_text(json.dumps(record))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    if os.name == "nt":
        monkeypatch.setenv("HOMEDRIVE", home.drive)
        monkeypatch.setenv("HOMEPATH", str(home)[len(home.drive):])
    root = tmp_path_factory.mktemp("app")
    if os.name == "nt":
        repair_path = root / "versions" / (selection["runtime_version"] + ".repair." + "0" * 32)
        root = root.with_name(root.name + "x" * max(0, 270 - len(str(repair_path))))
    options = dict(
        uv=Path(shutil.which("uv")), base_python=Path(sys.executable),
        health_module="demo_app", expected_version=selection["runtime_version"],
        expected_hash=selection["payload_hash"],
    )
    result = source.install_application(snapshot, "demo-app", selected.payload, root, **options)
    slot = root / "versions" / selection["runtime_version"]
    assert result == {"state": "installed", "slot": str(slot)}
    for helper in (slot / ".runtime-bootstrap").iterdir():
        if helper.name == "runtime-generation":
            assert re.fullmatch(rb"[0-9a-f]{32}\n", helper.read_bytes())
        else:
            assert helper.read_bytes() == (slot / "runtime" / helper.name).read_bytes()
    before = {name: (root / name).read_bytes() for name in ("current-version", "last-known-good")}
    assert json.loads((root / "setup.json").read_text()) == ["--setup"]
    assert json.loads((root / "started.json").read_text()) == ["--start"]
    assert Path((root / "callback-cwd").read_text()).samefile(slot)
    marker = source.engine.runtime.marker_path(root, slot.name).read_bytes()
    installed_declaration = (slot / "application.json").read_bytes()
    assert source.install_application(snapshot, "demo-app", selected.payload, root, **options) == result
    assert source.engine.runtime.marker_path(root, slot.name).read_bytes() == marker
    assert {name: (root / name).read_bytes() for name in before} == before
    assert (slot / "application.json").read_bytes() == installed_declaration
    assert json.loads((root / "setup.json").read_text()) == ["--setup"]
    assert json.loads((root / "started.json").read_text()) == ["--start"]
    assert Path((root / "callback-cwd").read_text()).samefile(slot)
    record.update(setup=["--setup", "--new-setup"], start=["--start", "--new-start"])
    path.write_text(json.dumps(record))
    automatic = source.install_application(snapshot, "demo-app", selected.payload, root, **options)
    automatic_slot = Path(automatic["slot"])
    assert automatic_slot != slot and ".repair." in automatic_slot.name
    assert json.loads((root / "setup.json").read_text()) == ["--setup", "--new-setup"]
    assert json.loads((root / "started.json").read_text()) == ["--start", "--new-start"]
    automatic_selectors = {name: (root / name).read_bytes() for name in before}
    automatic_declaration = (automatic_slot / "application.json").read_bytes()
    automatic_marker = source.engine.runtime.marker_path(root, automatic_slot.name).read_bytes()
    assert source.install_application(
        snapshot, "demo-app", selected.payload, root, **options,
    ) == automatic
    assert {name: (root / name).read_bytes() for name in before} == automatic_selectors
    assert (automatic_slot / "application.json").read_bytes() == automatic_declaration
    assert source.engine.runtime.marker_path(root, automatic_slot.name).read_bytes() == automatic_marker
    assert json.loads((root / "setup.json").read_text()) == ["--setup", "--new-setup"]
    assert json.loads((root / "started.json").read_text()) == ["--start", "--new-start"]
    assert Path((root / "callback-cwd").read_text()).samefile(automatic_slot)
    before = {name: (root / name).read_bytes() for name in before}
    assert (slot / "application.json").read_bytes() == installed_declaration
    with pytest.raises(subprocess.CalledProcessError):
        source.install_application(
            snapshot, "demo-app", selected.payload, root,
            arguments=("--fail-start",), repair=True, **options,
        )
    failed = [path for path in (root / "versions").iterdir() if path not in (slot, automatic_slot)]
    assert len(failed) == 1 and not (failed[0] / "install-success.json").exists()
    assert (root / "current-version").read_text().strip() == failed[0].name
    assert (root / "last-known-good").read_bytes() == before["last-known-good"]
    repaired = source.install_application(
        snapshot, "demo-app", selected.payload, root, repair=True, **options,
    )
    repaired_slot = Path(repaired["slot"])
    assert repaired_slot != slot and ".repair." in repaired_slot.name
    if os.name == "nt":
        assert len(str(repaired_slot)) >= 270
    assert Path((root / "callback-cwd").read_text()).samefile(repaired_slot)
    assert (root / "current-version").read_text().strip() == repaired_slot.name
    assert json.loads((root / "setup.json").read_text()) == ["--setup", "--new-setup"]
    assert json.loads((root / "started.json").read_text()) == ["--start", "--new-start"]
    assert (slot / "application.json").read_bytes() == installed_declaration
    shutil.rmtree(snapshot)
    launcher = home / ".local" / "bin" / ("demo-app.ps1" if os.name == "nt" else "demo-app")
    command = (
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-File", str(launcher)]
        if os.name == "nt" else [str(launcher)]
    )
    probe = subprocess.run(
        command, cwd=tmp_path, env=dict(os.environ), capture_output=True, text=True,
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.strip() == "prepared application ready"
    sequence = []
    for argv in activation_calls:
        scripts = [(index, Path(argument)) for index, argument in enumerate(argv)
                   if Path(argument).name == "install.py"]
        assert len(scripts) == 1, (
            f"owner_checks={len(activation_calls)} install_script_arguments={len(scripts)}"
        )
        index, script = scripts[0]
        sequence.append((script.parent.name, tuple(argv[index + 1:])))
    diagnostic = (
        f"owner_checks={len(activation_calls)} "
        f"sequence={[(name[-80:], args[:4]) for name, args in sequence[:8]]!r}"
    )
    assert sequence == [
        (slot.name, ("--",)), (slot.name, ("--",)),
        (automatic_slot.name, ("--",)), (automatic_slot.name, ("--",)),
        (failed[0].name, ("--", "--fail-start")), (repaired_slot.name, ("--",)),
    ], diagnostic
    assert len(activation_calls) == 6, diagnostic
    assert len({name for name, _arguments in sequence}) == 4, diagnostic


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("native_source", [False, True], indirect=True)
def test_native_candidate_wheel_handoff(
    native_source, tmp_path, repair,
):
    source, snapshot = native_source
    uv = shutil.which("uv")
    assert uv, "native UV required"
    script = Path(source.__file__).resolve()
    selected = source.select_project(snapshot, "demo-app")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    inspect = [
        sys.executable, "-I", "-B", str(script), "--install-source", str(snapshot),
        "--component", "demo-app", "--payload", str(selected.payload), "--native-inputs",
    ]
    result = subprocess.run(inspect, capture_output=True, text=True, creationflags=flags, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    selection = json.loads(result.stdout)
    root = tmp_path
    arguments = [
        *inspect[:-1], "--native-session", "--root", str(root), "--uv", uv,
        "--base-python", sys.executable, "--health-module", "demo_app",
        "--expected-runtime-version", selection["runtime_version"],
        "--expected-payload-hash", selection["payload_hash"],
        *(["--repair"] if repair else []),
    ]
    commands = [
        {"command": "complete", "payload_hash": selection["payload_hash"], "retain_helpers": True},
        {"command": "settled"},
    ]
    result = subprocess.run(
        arguments, input="".join(json.dumps(command) + "\n" for command in commands),
        capture_output=True, text=True, creationflags=flags, timeout=120,
        env={**os.environ, "UV_OFFLINE": "1", "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records[0]["version"].startswith(selection["runtime_version"])
    assert (".repair." in records[0]["version"]) is repair
    assert records[1:] == [{"complete": True}, {"settled": True}]
    assert source.engine.runtime.is_complete(root, records[0]["version"], expect_hash=selection["payload_hash"])
    assert not (root / "current-version").exists(), "construction must not publish native selectors"
    python = source.engine.runtime.slot_python(root, records[0]["version"])
    slot = root / "versions" / records[0]["version"]
    if "application" in selected.project:
        assert python.parent.parent == slot / ".venv"
        assert (slot / "install.ps1").is_file() and (slot / "uv.lock").is_file()
        assert not (slot / "pyvenv.cfg").exists()
        assert not (slot / "install-success.json").exists()
    executable = str(source.engine._native_retained_path(python))
    result = subprocess.run(
        [executable, "-I", "-c", "import demo_app; assert demo_app.VALUE == 1"],
        executable=executable,
        capture_output=True, check=True, creationflags=flags, timeout=30,
    )
    assert result.returncode == 0


@pytest.mark.parametrize("native_source", [True], indirect=True)
@pytest.mark.parametrize("changed", ["wheel", "installed-content", "origin"])
def test_staged_application_reuse_checks_pinned_wheels_and_installed_record(
    native_source, tmp_path, monkeypatch, changed,
):
    source, snapshot = native_source
    monkeypatch.setenv("UV_OFFLINE", "1")
    uv = shutil.which("uv")
    assert uv, "native UV required"
    root = tmp_path / "runtime"
    with source.project_inputs(snapshot, "demo-app") as (selected, inputs):
        selection = source.native_selection(selected, inputs)
        options = {
            "plugin": selected.payload, "uv": Path(uv), "base_python": Path(sys.executable),
            "health_module": "demo_app", "local_packages": tuple(selection["local_packages"]),
            "wheel_inputs": inputs, "application": snapshot / selected.project["application"],
        }
        with source.engine.numbered_candidate(root, selection["runtime_version"], **options) as ready:
            slot = ready.slot
            marker = source.engine.runtime.marker_path(root, slot.name)
            before = marker.read_bytes()
        version = ".".join(map(str, sys.version_info[:2]))
        site = slot / ".venv" / (
            "Lib/site-packages" if os.name == "nt" else f"lib/python{version}/site-packages"
        )
        if changed == "wheel":
            path = slot / "wheels" / "demo_app-1.0.0-py3-none-any.whl"
            path.write_bytes(path.read_bytes() + b"changed")
        elif changed == "installed-content":
            (site / "demo_app" / "__init__.py").write_bytes(b"VALUE = 2\n")
        else:
            origin = site / "demo_app-1.0.0.dist-info" / "direct_url.json"
            record = json.loads(origin.read_text())
            record["url"] = (slot / "foreign.whl").as_uri()
            origin.write_text(json.dumps(record))
        monkeypatch.setattr(source.engine, "_construct", lambda *a, **kw: pytest.fail(
            "immutable application reuse attempted to reinstall",
        ))
        with pytest.raises((RuntimeError, source.engine.HealthRepairRequired), match="Application|health"):
            with source.engine.numbered_candidate(root, selection["runtime_version"], **options):
                pytest.fail("changed installed application was accepted")
        assert marker.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell constructor handoff")
@pytest.mark.parametrize("native_source", [True], indirect=True)
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("repair", [False, True])
def test_native_source_shell_handoff(native_source, tmp_path, shell, repair):
    source, snapshot = native_source
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    uv = shutil.which("uv")
    assert uv, "native UV required"
    with source.project_inputs(snapshot, "demo-app") as (selected, inputs):
        selection = source.native_selection(selected, inputs)
    helper = Path(source.__file__).with_name("versioned_runtime.py")
    renderer = source.engine.runtime._runtime_component("versioned_runtime_construction")
    incumbent = tmp_path / "versions" / selection["runtime_version"]
    old_files = {}
    if repair:
        incumbent.mkdir(parents=True)
        (incumbent / ".install-complete.json").write_bytes(b"corrupt incumbent evidence")
        (incumbent / "keep.txt").write_bytes(b"untouched incumbent")
        (tmp_path / "current-version").write_text(selection["runtime_version"], encoding="utf-8")
        old_files = {path.name: path.read_bytes() for path in incumbent.iterdir()}

    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    script = tmp_path / "handoff.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + renderer.construction_shell_helpers("ps1")
        + "\ntry {\n"
        + "$reuse = Start-RuntimeConstruction "
        + f"-Python {quote(sys.executable)} -Helper {quote(helper)} -Root {quote(tmp_path)} "
        + f"-Version {quote(selection['runtime_version'])} -PayloadHash {quote(selection['payload_hash'])} "
        + f"-LinkName 'venv' -InstallSource {quote(snapshot)} -Payload {quote(selected.payload)} "
        + f"-Plugin 'demo-app' -UvCommand {quote(uv)} -HealthModule 'demo_app'"
        + (" -Repair" if repair else "") + "\n"
        + "if (-not $reuse -or $script:RuntimeConstructionSession.Process.HasExited) { throw 'No retained candidate' }\n"
        + "Write-Output ('SELECTED=' + $script:RuntimeConstructionSession.Version)\n"
        + f"Complete-RuntimeConstruction -PayloadHash {quote(selection['payload_hash'])} -RetainHelpers\n"
        + "} finally { Stop-RuntimeConstruction }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=120,
        env={**os.environ, "UV_OFFLINE": "1", "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    version = next(line.removeprefix("SELECTED=") for line in result.stdout.splitlines() if line.startswith("SELECTED="))
    assert version.startswith(selection["runtime_version"])
    assert (".repair." in version) is repair
    assert source.engine.runtime.is_complete(
        tmp_path, version, expect_hash=selection["payload_hash"],
    )
    assert source.engine.runtime.slot_python(tmp_path, version).parent.parent == (
        tmp_path / "versions" / version / ".venv"
    )
    if repair:
        assert {path.name: path.read_bytes() for path in incumbent.iterdir()} == old_files
        assert (tmp_path / "current-version").read_text() == selection["runtime_version"]
    else:
        assert not (tmp_path / "current-version").exists()
