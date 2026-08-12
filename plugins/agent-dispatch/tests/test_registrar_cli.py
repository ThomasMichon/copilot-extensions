"""Tests for the `registrar` CLI group (pointer registry + discovery over the CLI)."""

from __future__ import annotations

import json

from agent_dispatch.__main__ import main
from agent_dispatch.registrar_discovery import REGISTRAR_DIR_ENV


def _run(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr().out
    return rc, out


def test_registrar_add_list_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    decls = tmp_path / "decls"
    decls.mkdir()
    rc, out = _run(["registrar", "add-pointer", "general", str(decls)], capsys)
    assert rc == 0
    assert json.loads(out)["name"] == "general"

    rc, out = _run(["registrar", "list"], capsys)
    assert [p["name"] for p in json.loads(out)] == ["general"]

    rc, out = _run(["registrar", "remove", "general"], capsys)
    assert json.loads(out) == {"removed": True}

    rc, out = _run(["registrar", "list"], capsys)
    assert json.loads(out) == []


def test_registrar_add_pointer_bad_name_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    rc = main(["registrar", "add-pointer", "bad name", str(tmp_path)])
    assert rc == 2


def test_registrar_discover(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    decls = tmp_path / "decls"
    decls.mkdir()
    (decls / "general.json").write_text(
        json.dumps({"name": "general", "labels": ["general"], "concurrency": 2}),
        encoding="utf-8",
    )
    _run(["registrar", "add-pointer", "general", str(decls)], capsys)
    rc, out = _run(["registrar", "discover"], capsys)
    data = json.loads(out)
    assert rc == 0
    assert data[0]["name"] == "general"
    assert data[0]["concurrency"] == 2
    assert data[0]["filters"]["permit"]["task-type"] == ["general"]


def test_registrar_discover_rejects_duplicates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    d1 = tmp_path / "one"
    d2 = tmp_path / "two"
    d1.mkdir()
    d2.mkdir()
    (d1 / "general.json").write_text(json.dumps({"name": "general"}), encoding="utf-8")
    (d2 / "general.json").write_text(json.dumps({"name": "general"}), encoding="utf-8")
    _run(["registrar", "add-pointer", "one", str(d1)], capsys)
    _run(["registrar", "add-pointer", "two", str(d2)], capsys)
    rc = main(["registrar", "discover"])
    assert rc == 2  # duplicate profile name across sources


def test_registrar_discover_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    reg = tmp_path / "myrepo" / ".agent-dispatch" / "registrar"
    reg.mkdir(parents=True)
    (reg / "review.yaml").write_text("name: review\nlabels: [review]\n", encoding="utf-8")
    rc, out = _run(["registrar", "discover-repo", str(tmp_path / "myrepo")], capsys)
    data = json.loads(out)
    assert rc == 0
    assert data[0]["name"] == "review"
    assert data[0]["owner"] == "repo:myrepo"
