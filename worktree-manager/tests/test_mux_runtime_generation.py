"""The package version is not a constructed runtime's immutable identity."""

import sys

import pytest
from worktree_manager import __version__, mux_daemon
from worktree_manager import mux_daemon_cutover as cutover
from worktree_manager.runtime_identity import route_version, running_generation
from zdd import routing


def test_health_reports_own_prefix_generation_not_new_selection(tmp_path, monkeypatch):
    old = tmp_path / "versions" / "1.2.3+repair.old"
    old.mkdir(parents=True)
    (tmp_path / "current-version").write_text("1.2.3+repair.new\n")
    monkeypatch.setattr(sys, "prefix", str(old))
    runtime = mux_daemon.MuxDaemonRuntime(tmp_path / "registry.json", root=tmp_path)
    health = runtime.health()
    assert health["version"] == __version__
    assert health.get("runtime_generation") == old.name


def test_route_label_alone_cannot_attest_target_generation(tmp_path, monkeypatch):
    target = "1.2.3+repair.new"
    route = routing.Endpoint(bind="127.0.0.1", port=1234, version=target)
    monkeypatch.setattr(cutover.ControlClient, "health", lambda _: {
        "status": "ready", "version": "1.2.3", "runtime_generation": "1.2.3+repair.old",
    })
    assert not cutover._already_at_version(route, target, root=tmp_path)


@pytest.mark.parametrize("relative,expected", [
    ("versions/1.2.3+repair.old", "1.2.3+repair.old"),
    ("versions/1.2.3/.venv", "1.2.3"),
    ("source/.venv", None),
])
def test_identity_uses_interpreter_layout_and_preserves_source_version(
    tmp_path, monkeypatch, relative, expected,
):
    prefix = tmp_path.joinpath(*relative.split("/"))
    prefix.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    assert running_generation() == expected
    assert route_version() == (expected or __version__)


def test_wrong_target_generation_rolls_back_without_misclassifying_old_health(tmp_path, monkeypatch):
    from test_mux_daemon_cutover_helper import _FakeMuxDaemon, _Handle

    token = cutover.load_or_create_control_token(tmp_path)
    old = _FakeMuxDaemon(101, token=token, generation="1.2.3+repair.old")
    wrong = _FakeMuxDaemon(202, token=token, generation="1.2.3+repair.wrong")
    try:
        old.start(cutover.pick_free_port())
        cutover.publish_route(old.port, root=tmp_path, pid=old.pid, version=old.generation)

        def spawn(slot, *, root, port):
            wrong.start(port)
            return _Handle(wrong)

        monkeypatch.setattr(cutover, "spawn_passive", spawn)
        result = cutover.activate_after_update(
            root=tmp_path, slot=tmp_path / "versions" / "1.2.3+repair.new",
            version="1.2.3+repair.new", health_timeout=0.2, drain_timeout=0.2,
        )
        assert not result["result"]["ok"], result
        assert result["result"]["rolled_back"], result
        assert old.alive and not wrong.alive
        assert cutover.routed_endpoint(tmp_path).version == old.generation
        assert cutover._health_check("127.0.0.1", old.port, root=tmp_path)
    finally:
        wrong.force_terminate()
        old.force_terminate()
