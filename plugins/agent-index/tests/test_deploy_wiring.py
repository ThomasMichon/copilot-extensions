from __future__ import annotations

import json

from agent_index import __main__


def test_deploy_wires_cutover_orchestrator(monkeypatch, tmp_path, capsys) -> None:
    captured = {}

    class FakeResult:
        def __init__(self) -> None:
            self.ok = True
            self.steps = ["ok"]
            self.new_port = 4444
            self.rolled_back = False
            self.error = None

        def to_dict(self):
            return {"ok": True, "new_port": self.new_port, "steps": self.steps}

    class FakeOrchestrator:
        def __init__(self, config_dir, **kwargs):
            captured["config_dir"] = config_dir
            captured.update(kwargs)

        def run(self, **kwargs):
            captured["run"] = kwargs
            return FakeResult()

    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "zdd.breadcrumb.recover_stale_cutover",
        lambda *a, **k: {"recovered": False},
    )
    monkeypatch.setattr("zdd.cutover.CutoverOrchestrator", FakeOrchestrator)

    rc = __main__.main([
        "deploy",
        "--json",
        "--health-timeout",
        "7",
        "--drain-timeout",
        "11",
        "--force",
    ])

    assert rc == 0
    assert captured["config_dir"] == tmp_path / "home"
    assert captured["bind"] == "127.0.0.1"
    assert captured["version"] == __main__.__version__
    for key in ("spawn_passive", "health_check", "make_client", "pick_free_port"):
        assert callable(captured[key])
    assert captured["run"] == {"health_timeout": 7.0, "drain_timeout": 11.0, "force": True}
    assert json.loads(capsys.readouterr().out)["ok"] is True
