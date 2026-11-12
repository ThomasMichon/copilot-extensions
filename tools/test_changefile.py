"""Tests for tools/changefile.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import changefile


@pytest.fixture(autouse=True)
def _isolated_changefiles_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / ".changefiles"
    monkeypatch.setattr(changefile, "CHANGEFILES_DIR", d)
    return d


def test_write_and_read_changefile(_isolated_changefiles_dir: Path):
    path = changefile.write_changefile(
        [{"plugin": "agent-worktrees", "type": "patch"}], "Fix a bug"
    )
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["comment"] == "Fix a bug"
    assert data["changes"] == [{"plugin": "agent-worktrees", "type": "patch"}]

    pending = changefile.read_changefiles()
    assert len(pending) == 1
    assert pending[0][1]["comment"] == "Fix a bug"


def test_read_changefiles_empty_dir_returns_empty(_isolated_changefiles_dir: Path):
    assert changefile.read_changefiles() == []


def test_read_changefiles_skips_unreadable_file(_isolated_changefiles_dir: Path, capsys):
    _isolated_changefiles_dir.mkdir(parents=True)
    bad = _isolated_changefiles_dir / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    good = changefile.write_changefile([{"plugin": "x", "type": "dev"}], "ok")
    pending = changefile.read_changefiles()
    assert [p for p, _ in pending] == [good]
    assert "skipping unreadable" in capsys.readouterr().err


def test_parallel_changefiles_preserve_order_and_unreadable_diagnostics(
    _isolated_changefiles_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    directory = _isolated_changefiles_dir
    directory.mkdir()
    for index in reversed(range(96)):
        (directory / f"{index:03}.json").write_text(
            "{bad" if index % 17 == 0 else json.dumps({"comment": f"entry {index}"}),
            encoding="utf-8",
        )
    (directory / "directory.json").mkdir()
    serial = changefile.read_changefiles()
    diagnostics = capsys.readouterr().err
    original = changefile._read_changefile
    threads = set()

    def read(path):
        import threading

        threads.add(threading.current_thread().name)
        return original(path)

    monkeypatch.setattr(changefile, "_read_changefile", read)
    assert changefile.read_changefiles(parallel=True) == serial
    assert capsys.readouterr().err == diagnostics
    assert threads and all(name.startswith("ThreadPoolExecutor") for name in threads)


def test_cmd_add_writes_multiple_pairs(_isolated_changefiles_dir: Path):
    code = changefile.main([
        "add", "--plugin", "agent-worktrees", "--type", "patch",
        "--plugin", "agent-bridge", "--type", "dev",
        "--comment", "Shared fix",
    ])
    assert code == 0
    pending = changefile.read_changefiles()
    assert len(pending) == 1
    assert pending[0][1]["changes"] == [
        {"plugin": "agent-worktrees", "type": "patch"},
        {"plugin": "agent-bridge", "type": "dev"},
    ]


def test_cmd_add_rejects_mismatched_pairs(_isolated_changefiles_dir: Path, capsys):
    code = changefile.main([
        "add", "--plugin", "agent-worktrees", "--type", "patch", "--type", "dev",
        "--comment", "bad",
    ])
    assert code == 1
    assert "matching pairs" in capsys.readouterr().err


def test_cmd_add_rejects_invalid_type(_isolated_changefiles_dir: Path, capsys):
    code = changefile.main([
        "add", "--plugin", "agent-worktrees", "--type", "huge", "--comment", "bad",
    ])
    assert code == 1
    assert "invalid --type" in capsys.readouterr().err


def test_cmd_list_reports_pending(_isolated_changefiles_dir: Path, capsys):
    changefile.write_changefile([{"plugin": "agent-worktrees", "type": "patch"}], "Fix X")
    code = changefile.main(["list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "agent-worktrees=patch" in out
    assert "Fix X" in out
