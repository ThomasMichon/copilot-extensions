"""Whole-marketplace candidate build/verification without containers or live state."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

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
    _git(root, "init", "--quiet")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "--quiet", "-m", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


def test_canonical_entrypoint_builds_committed_standalone_tree(
    source: tuple[Path, str], tmp_path: Path, monkeypatch, capsys,
) -> None:
    root, head = source
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


@pytest.mark.parametrize("fault", [
    "missing", "wrong-head", "wrong-declared-head", "stale-manifest",
    "tamper", "extra", "removed", "mode", "receipt-link", "escaping-link",
    "nonobject-receipt", "missing-paths",
])
def test_candidate_fails_closed(
    source: tuple[Path, str], tmp_path: Path, fault: str,
) -> None:
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
