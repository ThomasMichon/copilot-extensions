"""Whole-marketplace candidate build/verification without containers or live state."""
from __future__ import annotations

from collections.abc import Iterator
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import materialize_main as mm  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "candidate_provenance", TOOLS / "clean-room/lib/materialized_provenance.py",
)
assert SPEC and SPEC.loader
PROVENANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROVENANCE)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        text=True, timeout=10,
    ).stdout.strip()


def test_manifest_prunes_directory_aliases_and_cycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "tree"
    (root / "real").mkdir(parents=True)
    (root / "real/file").write_bytes(b"owned bytes")
    try:
        (root / "alias").symlink_to("real", target_is_directory=True)
        (root / "cycle").symlink_to(".", target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("native symlink creation unavailable")
        raise
    reads: list[Path] = []
    original = Path.read_bytes

    def read(path: Path) -> bytes:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    manifest = PROVENANCE.content_manifest(root)
    assert set(manifest) == {"real", "real/file", "alias", "cycle"}
    assert manifest["alias"] == {"kind": "symlink", "target": "real"}
    assert manifest["cycle"] == {"kind": "symlink", "target": "."}
    assert reads == [root / "real/file"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"never read")
    (root / "escaping").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="link escapes root"):
        PROVENANCE.content_manifest(root)
    assert outside / "secret" not in reads


def test_manifest_refuses_unreadable_subtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "tree"
    unreadable = root / "unreadable"
    unreadable.mkdir(parents=True)
    original = os.scandir

    def scan(path: str | os.PathLike[str] | int) -> Iterator[os.DirEntry[str]]:
        if not isinstance(path, int) and Path(path) == unreadable:
            raise PermissionError("fixture subtree is unreadable")
        return original(path)

    monkeypatch.setattr(PROVENANCE.os, "scandir", scan)
    with pytest.raises(PermissionError, match="fixture subtree is unreadable"):
        PROVENANCE.content_manifest(root)


@pytest.mark.parametrize("inside", [False, True], ids=["outside", "inside"])
def test_manifest_refuses_hardlinks_before_read(tmp_path, monkeypatch, inside):
    root = tmp_path / "tree"
    root.mkdir()
    artifact = root / "file"
    artifact.write_bytes(b"shared inode")
    alias = root / "alias" if inside else tmp_path / "alias"
    os.link(artifact, alias)
    assert artifact.stat().st_nlink == 2

    def refuse_read(path):
        raise AssertionError(f"hardlinked content must not be read: {path}")

    monkeypatch.setattr(Path, "read_bytes", refuse_read)
    with pytest.raises(ValueError, match="materialized file must be single-link"):
        PROVENANCE.content_manifest(root)


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction")
@pytest.mark.parametrize("at_root", [False, True], ids=["subtree", "root"])
def test_manifest_refuses_directory_junctions(tmp_path: Path, at_root: bool) -> None:
    shell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    root = tmp_path / "tree"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"never included")
    if not at_root:
        root.mkdir()
    link = root if at_root else root / "junction"
    result = subprocess.run(
        [shell, "-NoProfile", "-Command",
         "New-Item -ItemType Junction -Path $env:MANIFEST_TEST_LINK "
         "-Target $env:MANIFEST_TEST_TARGET -ErrorAction Stop | Out-Null"],
        env={**os.environ, "MANIFEST_TEST_LINK": str(link),
             "MANIFEST_TEST_TARGET": str(outside)},
        capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert getattr(link.lstat(), "st_file_attributes", 0) & 0x400
    with pytest.raises(ValueError, match="reparse path|real directory"):
        PROVENANCE.content_manifest(root)


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source with spaces"
    root.mkdir()
    plugin = root / "plugins/example"
    (plugin / "docs").mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"example","version":"1.0.0"}\n')
    (plugin / "docs/shared.md").write_text(
        "<!-- VENDOR_POINTER: source=docs/shared.md kind=file -->\n",
    )
    (root / "docs").mkdir()
    (root / "docs/shared.md").write_text("# Materialized\n")
    (root / ".github/plugin").mkdir(parents=True)
    (root / ".github/plugin/marketplace.json").write_text('{"plugins":[]}\n')
    (root / "tools").mkdir()
    for name in (
        "materialize_main.py", "materialize_installer_engine.py",
        "materialize_launch_wrapper_assets.py", "nested_uv_editable_ref.py",
        "uv_editable_ref.py", "launch_wrapper_assets_ref.py",
        "installer_engine_ref.py", "standalone_consumers.py",
    ):
        shutil.copy2(TOOLS / name, root / "tools" / name)
    _git(root, "init", "--quiet")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "--quiet", "-m", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


def test_canonical_entrypoint_builds_committed_standalone_tree(
    source: tuple[Path, str], tmp_path: Path, monkeypatch, capsys,
) -> None:
    root, head = source
    # Older supported tarfile APIs have no filter keyword; never call extractall.
    def old_extractall(self, path=".", members=None, *, numeric_owner=False):
        raise AssertionError("candidate builder must not use extractall")
    monkeypatch.setattr(tarfile.TarFile, "extractall", old_extractall)
    (root / "docs/shared.md").write_text("uncommitted tamper\n")
    (root / "untracked").write_text("not committed\n")
    monkeypatch.setattr(mm, "REPO", root)
    dest = tmp_path / "candidate with spaces"
    assert mm.main(["--candidate", "--dest", str(dest)]) == 0
    assert "Materialized" in capsys.readouterr().out
    assert not (dest / ".git").exists()
    assert not (dest / "untracked").exists()
    assert (dest / "plugins/example/docs/shared.md").read_text() == "# Materialized\n"
    copy = tmp_path / "standalone copy"
    shutil.copytree(dest, copy)
    assert PROVENANCE.verify(copy, head) == head
    receipt = json.loads((copy / PROVENANCE.RECEIPT).read_text())
    assert receipt["source_commit"] == head
    assert ".github/plugin/marketplace.json" in receipt["paths"]
    assert "plugins/example/docs/shared.md" in receipt["paths"]
    assert PROVENANCE.RECEIPT not in receipt["paths"]
    with pytest.raises(ValueError, match="already exist"):
        PROVENANCE.build(dest, source_root=root)


def test_builder_does_not_inherit_repository_selection(source, tmp_path, monkeypatch):
    root, head = source
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "wrong-tree"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(tmp_path / "wrong-objects"))
    dest = tmp_path / "candidate"
    PROVENANCE.build(dest, source_root=root)
    assert PROVENANCE.verify(dest, head) == head


@pytest.mark.parametrize("module", ["materialize_main.py", "uv_editable_ref.py"])
def test_builder_uses_committed_materializer_code_not_working_tree(source, tmp_path, module, monkeypatch):
    root, head = source
    (root / "tools" / module).write_text(
        "raise AssertionError('dirty transformation code must never execute')\n",
    )
    monkeypatch.setattr(mm, "build", lambda *_a, **_k: pytest.fail("ambient build was invoked"))
    dest = tmp_path / "candidate"
    PROVENANCE.build(dest, source_root=root)
    assert PROVENANCE.verify(dest, head) == head
    assert (dest / "plugins/example/docs/shared.md").read_text() == "# Materialized\n"
    assert "dirty transformation" not in (dest / "tools" / module).read_text()


def test_builder_refuses_destination_created_during_archive_preparation(source, tmp_path, monkeypatch):
    root, _ = source
    dest = tmp_path / "candidate"
    original = PROVENANCE.extract_source

    def extract(tree, snapshot):
        original(tree, snapshot)
        dest.mkdir()
        (dest / "sentinel").write_bytes(b"not owned by the builder")

    monkeypatch.setattr(PROVENANCE, "extract_source", extract)
    with pytest.raises(ValueError, match="candidate destination must not already exist"):
        PROVENANCE.build(dest, source_root=root)
    assert (dest / "sentinel").read_bytes() == b"not owned by the builder"
    assert not (dest / PROVENANCE.RECEIPT).exists()


def test_missing_committed_helper_has_no_ambient_fallback(source, tmp_path):
    root, _ = source
    (root / "tools/uv_editable_ref.py").unlink()
    _git(root, "add", "-u")
    _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "--quiet", "-m", "missing committed helper")
    dest = tmp_path / "candidate"
    with pytest.raises(ValueError, match="committed materializer failed"):
        PROVENANCE.build(dest, source_root=root)
    assert not (dest / PROVENANCE.RECEIPT).exists()


@pytest.mark.parametrize("kind", ["fifo", "hardlink"])
def test_receipt_special_or_multiple_link_file_fails_before_read(source, tmp_path, kind):
    root, head = source
    dest = tmp_path / "candidate"
    PROVENANCE.build(dest, source_root=root)
    receipt = dest / PROVENANCE.RECEIPT
    if kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        receipt.unlink()
        os.mkfifo(receipt)
    else:
        os.link(receipt, tmp_path / "receipt-alias")
    with pytest.raises(ValueError, match="single-link regular"):
        PROVENANCE.verify(dest, head)


def test_candidate_verification_refuses_external_hardlink_without_byte_changes(source, tmp_path):
    root, head = source
    dest = tmp_path / "candidate"
    PROVENANCE.build(dest, source_root=root)
    artifact = dest / "plugins/example/docs/shared.md"
    before = artifact.read_bytes()
    os.link(artifact, tmp_path / "external-alias")
    assert artifact.read_bytes() == before
    with pytest.raises(ValueError, match="materialized file must be single-link"):
        PROVENANCE.verify(dest, head)


@pytest.mark.parametrize("fault", [
    "missing", "wrong-head", "wrong-declared-head", "stale-manifest",
    "tamper", "extra", "removed", "mode", "receipt-link", "escaping-link",
    "nonobject-receipt", "missing-paths",
])
def test_candidate_fails_closed(
    source: tuple[Path, str], tmp_path: Path, fault: str,
) -> None:
    if fault == "mode" and os.name == "nt":
        pytest.skip("Windows chmod does not represent POSIX executable permissions")
    root, head = source
    dest = tmp_path / "candidate"
    PROVENANCE.build(dest, source_root=root)
    receipt_path = dest / PROVENANCE.RECEIPT
    receipt = json.loads(receipt_path.read_text())
    artifact = dest / "plugins/example/docs/shared.md"
    if fault == "missing":
        receipt_path.unlink()
    elif fault == "wrong-head":
        head = "0" * 40
    elif fault == "wrong-declared-head":
        receipt["source_commit"] = "0" * 40
        receipt_path.write_text(json.dumps(receipt))
    elif fault == "stale-manifest":
        receipt["paths"]["plugins/example/docs/shared.md"]["sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt))
    elif fault == "tamper":
        artifact.write_text("changed\n")
    elif fault == "extra":
        (dest / "unexpected").write_text("extra\n")
    elif fault == "removed":
        artifact.unlink()
    elif fault == "mode":
        artifact.chmod(0o755)
    elif fault == "receipt-link":
        outside = tmp_path / "receipt"
        receipt_path.rename(outside)
        receipt_path.symlink_to(outside)
    elif fault == "escaping-link":
        (dest / "foreign").symlink_to(root)
    elif fault == "nonobject-receipt":
        receipt_path.write_text("[]")
    elif fault == "missing-paths":
        del receipt["paths"]
        receipt_path.write_text(json.dumps(receipt))
    with pytest.raises((OSError, ValueError)):
        PROVENANCE.verify(dest, head)


def test_unresolved_materialization_never_emits_receipt(
    source: tuple[Path, str], tmp_path: Path,
) -> None:
    root, _ = source
    (root / "docs/shared.md").unlink()
    _git(root, "add", "-u")
    _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "--quiet", "-m", "missing canonical")
    dest = tmp_path / "refused"
    with pytest.raises(ValueError, match="unresolved"):
        PROVENANCE.build(dest, source_root=root)
    assert not (dest / PROVENANCE.RECEIPT).exists()


@pytest.mark.parametrize("fault", [
    "../escape", "/absolute", "safe/../../escape", "safe\\escape",
    "C:/escape", "safe/./escape", "safe//escape", "symlink", "hardlink",
    "character", "block", "fifo",
])
def test_archive_validation_rejects_unsafe_members_before_extraction(tmp_path, fault):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tree:
        good = tarfile.TarInfo("good")
        good.size = 4
        tree.addfile(good, io.BytesIO(b"good"))
        member = tarfile.TarInfo(fault)
        types = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                 "character": tarfile.CHRTYPE, "block": tarfile.BLKTYPE,
                 "fifo": tarfile.FIFOTYPE}
        member.type = types.get(fault, tarfile.REGTYPE)
        member.linkname = "../sentinel"
        tree.addfile(member)
    root = tmp_path / "root"
    root.mkdir()
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"unchanged")
    with tarfile.open(fileobj=io.BytesIO(archive.getvalue())) as tree:
        with pytest.raises(ValueError, match="unsafe source archive"):
            PROVENANCE.extract_source(tree, root)
    assert not list(root.iterdir())
    assert sentinel.read_bytes() == b"unchanged"


def test_archive_validation_rejects_preexisting_root_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tree:
        tree.addfile(tarfile.TarInfo("linked/escape"))
    with tarfile.open(fileobj=io.BytesIO(archive.getvalue())) as tree:
        with pytest.raises(ValueError, match="escapes root"):
            PROVENANCE.extract_source(tree, root)
    assert not list(outside.iterdir())
