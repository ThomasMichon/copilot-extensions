"""Tests for the `registrar` CLI group (pointer registry + discovery over the CLI)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import agent_dispatch.__main__ as cli
from agent_dispatch.__main__ import main
from agent_dispatch.registrar_discovery import REGISTRAR_DIR_ENV


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    rc = main(argv)
    out = capsys.readouterr().out
    return rc, out


def test_registrar_add_list_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    decls = tmp_path / "decls"
    decls.mkdir()
    rc, out = _run(["registrar", "add-pointer", "general", str(decls)], capsys)
    assert rc == 0
    assert json.loads(out)["name"] == "general"

    rc, out = _run(["registrar", "list"], capsys)
    assert [p["name"] for p in json.loads(out)] == ["general"]

    rc, out = _run(["registrar", "remove", "general", "--keep-registrations"], capsys)
    assert json.loads(out)["removed"] is True

    rc, out = _run(["registrar", "list"], capsys)
    assert json.loads(out) == []


class _FakeRegistrations:
    """Stand-in coordinator client holding registration rows keyed by id."""

    def __init__(self, rows: dict[str, str | None], *, failing: frozenset[str] = frozenset()) -> None:
        self.rows: dict[str, dict[str, Any]] = {
            rid: {"id": rid, "kind": "supervised-lane", "spec": {}, "machine": machine}
            for rid, machine in rows.items()
        }
        self.failing = failing
        self.client_kwargs: dict[str, Any] | None = None

    def __call__(self, args: object, **kwargs: Any) -> _FakeRegistrations:
        self.client_kwargs = kwargs
        return self

    def __enter__(self) -> _FakeRegistrations:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def list_registrations(self, **_kwargs: object) -> list[dict[str, Any]]:
        return list(self.rows.values())

    def remove_registration(self, rid: str) -> dict[str, Any]:
        if rid in self.failing:
            raise RuntimeError(f"cannot remove {rid}")
        del self.rows[rid]
        return {"removed": True, "id": rid}


@pytest.fixture
def registrar_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISE_MACHINE", "host-a")
    return tmp_path


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeRegistrations) -> _FakeRegistrations:
    monkeypatch.setattr(cli, "_client", fake)
    return fake


def test_registrar_remove_withdraws_only_its_declared_registrations(
    registrar_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _install(monkeypatch, _FakeRegistrations({
        "declared:hu-e2e:hub-health": "host-a", "declared:hu-e2e:repo-sync": "HOST-A",
        "declared:hu-e2e-other:hub-health": "host-a", "declared:repo:other:hub-health": "host-a",
        "general-task-worker": "host-a",
    }))
    _run(["registrar", "add-pointer", "hu-e2e", str(registrar_env), "--owner", "hu-e2e"], capsys)

    rc, out = _run(["registrar", "remove", "hu-e2e"], capsys)

    data = json.loads(out)
    assert rc == 0 and data["removed"] is True
    assert data["registrations"]["status"] == "ok"
    assert data["registrations"]["machine"] == "host-a"
    assert data["registrations"]["removed"] == [
        "declared:hu-e2e:hub-health", "declared:hu-e2e:repo-sync",
    ]
    assert sorted(fake.rows) == [
        "declared:hu-e2e-other:hub-health", "declared:repo:other:hub-health",
        "general-task-worker",
    ]
    assert fake.client_kwargs == {"ensure": False, "allow_ssh_failover": False}


def test_registrar_remove_matches_colon_bearing_owner_exactly(
    registrar_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The default owner `pointer:foo` is a prefix of owner `pointer:foo:bar`;
    # only foo's own `declared:pointer:foo:<name>` rows may be withdrawn.
    fake = _install(monkeypatch, _FakeRegistrations({
        "declared:pointer:foo:lane": "host-a", "declared:pointer:foo:bar:lane": "host-a",
    }))
    fake.rows["declared:pointer:foo:other"] = {
        "id": "declared:pointer:foo:other", "machine": "host-a", "owner": "pointer:elsewhere",
    }
    _run(["registrar", "add-pointer", "foo", str(registrar_env)], capsys)

    rc, out = _run(["registrar", "remove", "foo"], capsys)

    data = json.loads(out)
    assert rc == 0
    assert data["registrations"]["owner"] == "pointer:foo"
    assert data["registrations"]["removed"] == ["declared:pointer:foo:lane"]
    assert sorted(fake.rows) == ["declared:pointer:foo:bar:lane", "declared:pointer:foo:other"]


def test_registrar_remove_leaves_rows_published_by_another_machine(
    registrar_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _install(monkeypatch, _FakeRegistrations({
        "declared:shared-owner:local": "host-a", "declared:shared-owner:remote": "host-b",
        "declared:shared-owner:unscoped": None,
    }))
    _run(["registrar", "add-pointer", "p", str(registrar_env), "--owner", "shared-owner"], capsys)

    rc = main(["registrar", "remove", "p"])
    captured = capsys.readouterr()

    data = json.loads(captured.out)
    assert rc == 0
    assert data["registrations"]["removed"] == [
        "declared:shared-owner:local", "declared:shared-owner:unscoped",
    ]
    assert data["registrations"]["other_machines"] == [
        {"id": "declared:shared-owner:remote", "machine": "host-b"},
    ]
    assert sorted(fake.rows) == ["declared:shared-owner:remote"]
    assert "declared:shared-owner:remote" in captured.err and "'host-b'" in captured.err

    rc, out = _run(
        ["registrar", "remove", "p", "--owner", "shared-owner", "--machine", "host-b"], capsys,
    )
    assert rc == 0
    assert json.loads(out)["registrations"]["removed"] == ["declared:shared-owner:remote"]
    assert fake.rows == {}


def test_registrar_remove_owner_cleans_orphans_and_keep_flag_skips(
    registrar_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _install(monkeypatch, _FakeRegistrations({
        "declared:pointer:gone:a": "host-a", "declared:pointer:kept:b": "host-a",
    }))
    _run(["registrar", "add-pointer", "kept", str(registrar_env)], capsys)

    rc, out = _run(["registrar", "remove", "kept", "--keep-registrations"], capsys)
    assert rc == 0 and json.loads(out) == {"removed": True}
    rc, out = _run(["registrar", "remove", "gone", "--owner", "pointer:gone"], capsys)
    data = json.loads(out)
    assert rc == 0 and data["removed"] is False
    assert data["registrations"]["removed"] == ["declared:pointer:gone:a"]
    assert sorted(fake.rows) == ["declared:pointer:kept:b"]


def test_registrar_remove_skips_owner_still_declared_by_another_pointer(
    registrar_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _install(monkeypatch, _FakeRegistrations({"declared:shared:a": "host-a"}))
    (registrar_env / "one").mkdir()
    (registrar_env / "two").mkdir()
    _run(["registrar", "add-pointer", "one", str(registrar_env / "one"), "--owner", "shared"], capsys)
    _run(["registrar", "add-pointer", "two", str(registrar_env / "two"), "--owner", "shared"], capsys)

    rc, out = _run(["registrar", "remove", "one"], capsys)

    data = json.loads(out)
    assert rc == 0
    assert data["registrations"]["status"] == "skipped"
    assert sorted(fake.rows) == ["declared:shared:a"]


def test_registrar_remove_partial_withdrawal_fails_with_warning(
    registrar_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _install(monkeypatch, _FakeRegistrations(
        {"declared:pointer:p:a": "host-a", "declared:pointer:p:b": "host-a"},
        failing=frozenset({"declared:pointer:p:b"}),
    ))
    _run(["registrar", "add-pointer", "p", str(registrar_env)], capsys)

    rc = main(["registrar", "remove", "p"])
    captured = capsys.readouterr()

    data = json.loads(captured.out)
    assert rc == 1 and data["removed"] is True
    assert data["registrations"]["status"] == "partial"
    assert data["registrations"]["removed"] == ["declared:pointer:p:a"]
    assert [f["id"] for f in data["registrations"]["failed"]] == ["declared:pointer:p:b"]
    assert sorted(fake.rows) == ["declared:pointer:p:b"]
    assert "WARNING" in captured.err and "declared:pointer:p:b" in captured.err
    assert "registrar remove p --owner pointer:p" in captured.err


def test_registrar_remove_succeeds_with_warning_when_coordinator_unavailable(
    registrar_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _down(args: object, **_kwargs: object) -> _FakeRegistrations:
        raise ConnectionRefusedError("coordinator down")

    monkeypatch.setattr(cli, "_client", _down)
    _run(["registrar", "add-pointer", "general", str(registrar_env)], capsys)

    rc = main(["registrar", "remove", "general"])
    captured = capsys.readouterr()

    data = json.loads(captured.out)
    assert rc == 0 and data["removed"] is True
    assert data["registrations"]["status"] == "coordinator_unavailable"
    assert "coordinator unreachable" in captured.err
    assert "registrar remove general --owner pointer:general" in captured.err

    rc = main(["registrar", "remove", "general", "--owner", "pointer:general"])
    captured = capsys.readouterr()
    assert rc == 1 and json.loads(captured.out)["removed"] is False
    assert "coordinator unreachable" in captured.err

def test_registrar_add_pointer_bad_name_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    rc = main(["registrar", "add-pointer", "bad name", str(tmp_path)])
    assert rc == 2


def test_registrar_discover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_registrar_discover_rejects_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_registrar_discover_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path))
    reg = tmp_path / "myrepo" / ".agent-dispatch" / "registrar"
    reg.mkdir(parents=True)
    (reg / "review.yaml").write_text("name: review\nlabels: [review]\n", encoding="utf-8")
    rc, out = _run(["registrar", "discover-repo", str(tmp_path / "myrepo")], capsys)
    data = json.loads(out)
    assert rc == 0
    assert data[0]["name"] == "review"
    assert data[0]["owner"] == "repo:myrepo"


def test_registrar_discover_repo_evaluator_kind_shows_real_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_registrar_discover_repo_surfaces_body_charter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
