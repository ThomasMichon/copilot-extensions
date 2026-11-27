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
    assert json.loads(out)["removed"] is True

    rc, out = _run(["registrar", "list"], capsys)
    assert json.loads(out) == []


class _FakeRegistrations:
    def __init__(self, ids):
        self.rows = {rid: {"id": rid, "kind": "supervised-lane", "spec": {}} for rid in ids}
        self.client_kwargs = None

    def __call__(self, args, **kwargs):
        self.client_kwargs = kwargs
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_registrations(self, **_kwargs):
        return list(self.rows.values())

    def remove_registration(self, rid):
        del self.rows[rid]
        return {"removed": True, "id": rid}


def test_registrar_remove_withdraws_only_its_declared_registrations(tmp_path, monkeypatch, capsys):
    import agent_dispatch.__main__ as m

    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    fake = _FakeRegistrations([
        "declared:hu-e2e:hub-health", "declared:hu-e2e:repo-sync",
        "declared:hu-e2e-other:hub-health", "declared:repo:other:hub-health",
        "general-task-worker",
    ])
    monkeypatch.setattr(m, "_client", fake)
    _run(["registrar", "add-pointer", "hu-e2e", str(tmp_path), "--owner", "hu-e2e"], capsys)

    rc, out = _run(["registrar", "remove", "hu-e2e"], capsys)

    data = json.loads(out)
    assert rc == 0 and data["removed"] is True
    assert data["registrations"]["status"] == "ok"
    assert data["registrations"]["removed"] == [
        "declared:hu-e2e:hub-health", "declared:hu-e2e:repo-sync",
    ]
    assert sorted(fake.rows) == [
        "declared:hu-e2e-other:hub-health", "declared:repo:other:hub-health",
        "general-task-worker",
    ]
    assert fake.client_kwargs == {"ensure": False, "allow_ssh_failover": False}


def test_registrar_remove_owner_cleans_orphans_and_keep_flag_skips(tmp_path, monkeypatch, capsys):
    import agent_dispatch.__main__ as m

    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    fake = _FakeRegistrations(["declared:pointer:gone:a", "declared:pointer:kept:b"])
    monkeypatch.setattr(m, "_client", fake)
    _run(["registrar", "add-pointer", "kept", str(tmp_path)], capsys)

    rc, out = _run(["registrar", "remove", "kept", "--keep-registrations"], capsys)
    assert json.loads(out) == {"removed": True}
    rc, out = _run(["registrar", "remove", "gone", "--owner", "pointer:gone"], capsys)
    data = json.loads(out)
    assert data["removed"] is False
    assert data["registrations"]["removed"] == ["declared:pointer:gone:a"]
    assert sorted(fake.rows) == ["declared:pointer:kept:b"]


def test_registrar_remove_skips_owner_still_declared_by_another_pointer(tmp_path, monkeypatch, capsys):
    import agent_dispatch.__main__ as m

    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    fake = _FakeRegistrations(["declared:shared:a"])
    monkeypatch.setattr(m, "_client", fake)
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    _run(["registrar", "add-pointer", "one", str(tmp_path / "one"), "--owner", "shared"], capsys)
    _run(["registrar", "add-pointer", "two", str(tmp_path / "two"), "--owner", "shared"], capsys)

    rc, out = _run(["registrar", "remove", "one"], capsys)

    data = json.loads(out)
    assert data["registrations"]["status"] == "skipped"
    assert sorted(fake.rows) == ["declared:shared:a"]


def test_registrar_remove_succeeds_when_coordinator_unavailable(tmp_path, monkeypatch, capsys):
    import agent_dispatch.__main__ as m

    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))

    def _down(args, **_kwargs):
        raise ConnectionRefusedError("coordinator down")

    monkeypatch.setattr(m, "_client", _down)
    _run(["registrar", "add-pointer", "general", str(tmp_path)], capsys)

    rc, out = _run(["registrar", "remove", "general"], capsys)

    data = json.loads(out)
    assert rc == 0 and data["removed"] is True
    assert data["registrations"]["status"] == "coordinator_unavailable"


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


def test_registrar_discover_repo_evaluator_kind_shows_real_fields(tmp_path, monkeypatch, capsys):
    """An evaluator-kind declaration's real ``kind``/``spec`` must surface
    through ``discover-repo`` -- never the generic supervised-lane
    pool-profile shape, which would make a correctly-parsed evaluator look
    like an inert, misconfigured worker-pool profile instead."""
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    reg = tmp_path / "myrepo" / ".agent-dispatch" / "registrar"
    reg.mkdir(parents=True)
    (reg / "my-evaluator.yaml").write_text(
        "\n".join(
            [
                "name: my-evaluator",
                "kind: evaluator",
                "spec:",
                "  repo: example.com/owner/name",
                "  evaluator_ref: my-evaluator",
                "  evaluator_spec:",
                "    scripts:",
                "      my-evaluator: [/usr/bin/python3, -m, my_module]",
                "    timeout_seconds: 30",
            ]
        ),
        encoding="utf-8",
    )
    rc, out = _run(["registrar", "discover-repo", str(tmp_path / "myrepo")], capsys)
    data = json.loads(out)
    assert rc == 0
    (decl,) = data
    assert decl["name"] == "my-evaluator"
    assert decl["kind"] == "evaluator"
    assert decl["spec"]["evaluator_ref"] == "my-evaluator"
    assert decl["spec"]["evaluator_spec"]["scripts"]["my-evaluator"] == [
        "/usr/bin/python3",
        "-m",
        "my_module",
    ]
    # Never the generic pool-profile shape for a non-lane kind.
    assert "body" not in decl
    assert "concurrency" not in decl


def test_registrar_discover_repo_surfaces_body_charter(tmp_path, monkeypatch, capsys):
    """A declared ``body.charter`` (and the label-override fields) must
    surface through ``discover-repo`` -- the summary previously hardcoded
    only ``type``/``agent``, silently dropping a correctly-parsed charter
    from view and making the venue-vs-charter split look unsupported to
    anyone debugging a declaration this way (the exact false signal that
    led a prior investigation to conclude this field didn't exist)."""
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    reg = tmp_path / "myrepo" / ".agent-dispatch" / "registrar"
    reg.mkdir(parents=True)
    (reg / "charter-worker.yaml").write_text(
        "\n".join(
            [
                "name: charter-worker",
                "labels: [charter-worker]",
                "body:",
                "  type: headless",
                "  agent: example-project@example-host",
                "  charter: charter-worker",
                "  disposable_cli_labels: [charter-worker]",
            ]
        ),
        encoding="utf-8",
    )
    rc, out = _run(["registrar", "discover-repo", str(tmp_path / "myrepo")], capsys)
    data = json.loads(out)
    assert rc == 0
    (decl,) = data
    assert decl["body"]["agent"] == "example-project@example-host"
    assert decl["body"]["charter"] == "charter-worker"
    assert decl["body"]["disposable_cli_labels"] == ["charter-worker"]
