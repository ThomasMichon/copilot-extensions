"""Tests for `agent-bridge create <target> "<prompt>" --cli`
(agent-bridge-cli-mode-sessions Phase 4): dispatches to a venue's own
CLI-mode `copilot` verb instead of the ordinary headless ACP session `create`
otherwise makes.
"""
from __future__ import annotations

import argparse

import pytest

from agent_bridge import __main__ as m
from agent_bridge import session_targeting_cli as targeting


def _ns(**kw):
    base = dict(
        target="codespace:friendly-eureka",
        prompt=None,
        prompt_file=None,
        cli=True,
        driver=None,
        caller=None,
        json=False,
        no_wait=False,
        model=None,
        effort=None,
        target_dir=None,
        worktree_id=None,
        session_id_file=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


class TestCmdCreateCliDispatch:
    def test_codespace_target_execs_agent_codespaces_copilot(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        seen = {}

        class _Result:
            returncode = 0

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return _Result()

        monkeypatch.setattr(targeting.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="codespace:friendly-eureka", prompt="do the thing"))

        assert exc.value.code == 0
        assert seen["argv"] == [
            "/bin/agent-codespaces", "copilot", "friendly-eureka",
            "--seed", "do the thing",
        ]

    def test_container_target_execs_agent_containers_copilot(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        seen = {}

        class _Result:
            returncode = 3

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return _Result()

        monkeypatch.setattr(targeting.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="container:repo-1", prompt=None))

        assert exc.value.code == 3
        assert seen["argv"] == ["/bin/agent-containers", "copilot", "repo-1"]

    def test_driver_is_forwarded(self, monkeypatch) -> None:
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        seen = {}

        class _Result:
            returncode = 0

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return _Result()

        monkeypatch.setattr(targeting.subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            m._cmd_create(
                _ns(target="codespace:cs-1", prompt="go", driver="orchestrator"),
            )

        assert seen["argv"] == [
            "/bin/agent-codespaces", "copilot", "cs-1",
            "--seed", "go", "--driver", "orchestrator",
        ]

    def test_bare_target_is_refused_with_a_helpful_pointer(
        self, monkeypatch, capsys,
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="my-worktree-id"))

        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "namespaced venue target" in err
        assert "agent-worktrees copilot" in err

    def test_unrecognized_prefix_is_refused(self, monkeypatch, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="machine:some-host"))

        assert exc.value.code == 2
        assert "namespaced venue target" in capsys.readouterr().err

    def test_charter_is_refused_with_cli(self, monkeypatch, capsys) -> None:
        """--cli's CLI-mode venue verbs have no charter-binding flag -- reject
        the combination explicitly rather than silently dropping --charter."""
        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="codespace:friendly-eureka", charter="cab-charter"))

        assert exc.value.code == 2
        assert "--charter is not supported with --cli" in capsys.readouterr().err

    def test_missing_binstub_fails_clearly(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(targeting.shutil, "which", lambda name: None)

        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="codespace:cs-1"))

        assert exc.value.code == 1
        assert "not found on PATH" in capsys.readouterr().err

    def test_prompt_file_is_honored_as_the_seed(self, monkeypatch, tmp_path) -> None:
        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("multi\nline\nprompt", encoding="utf-8")
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        seen = {}

        class _Result:
            returncode = 0

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return _Result()

        monkeypatch.setattr(targeting.subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            m._cmd_create(
                _ns(target="codespace:cs-1", prompt=None, prompt_file=str(prompt_path)),
            )

        assert seen["argv"] == [
            "/bin/agent-codespaces", "copilot", "cs-1",
            "--seed", "multi\nline\nprompt",
        ]

    def test_ordinary_create_without_cli_is_unaffected(self, monkeypatch) -> None:
        # Regression guard: adding --cli must not change any existing
        # headless-create behavior when the flag is absent (getattr default).
        from agent_bridge.client import BridgeClientError

        class _Client:
            def get_session(self, _target):
                raise BridgeClientError(404, "not found")

        monkeypatch.setattr(m, "_get_client", lambda: _Client())
        monkeypatch.setattr(
            "agent_bridge.resume_handoff_cli.find_singleton_repo",
            lambda target: None,
        )
        called = {}

        def fake_resolve_target(client, target, **kwargs):
            called["target"] = target
            return "session-abc"

        monkeypatch.setattr(m, "_resolve_target", fake_resolve_target)
        monkeypatch.setattr(m, "_connection_identity", lambda client, sid: {})
        monkeypatch.setattr(m, "_print_connection_identity", lambda ident: None)

        args = _ns(target="some-agent", cli=False)
        del args.cli  # simulate an argparse namespace predating --cli entirely
        m._cmd_create(args)

        assert called["target"] == "some-agent"


class TestCmdCreateCliDetach:
    def _capture(self, monkeypatch, rc=0):
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        seen = {}

        class _Result:
            returncode = rc

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return _Result()

        monkeypatch.setattr(targeting.subprocess, "run", fake_run)
        return seen

    def test_detach_forwards_flag_and_pipes_seed_over_stdin(self, monkeypatch) -> None:
        seen = self._capture(monkeypatch)
        prompt = 'line one\nline "two" with quotes'

        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(prompt=prompt, detach=True, driver="orchestrator"))

        assert exc.value.code == 0
        assert seen["argv"] == [
            "/bin/agent-codespaces", "copilot", "friendly-eureka",
            "--detach", "--seed-file", "-", "--driver", "orchestrator",
        ]
        assert seen["kwargs"]["input"] == prompt

    def test_detach_without_prompt_sends_no_seed(self, monkeypatch) -> None:
        seen = self._capture(monkeypatch)

        with pytest.raises(SystemExit):
            m._cmd_create(_ns(detach=True))

        assert seen["argv"] == [
            "/bin/agent-codespaces", "copilot", "friendly-eureka", "--detach",
        ]
        assert seen["kwargs"]["input"] is None

    def test_detach_refused_for_container_targets(self, monkeypatch, capsys) -> None:
        self._capture(monkeypatch)
        monkeypatch.setattr(
            targeting.subprocess, "run",
            lambda *a, **k: pytest.fail("must not launch"),
        )

        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="container:repo-1", detach=True))

        assert exc.value.code == 2
        assert "codespace" in capsys.readouterr().err

    def test_detach_requires_cli(self, monkeypatch, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(cli=False, detach=True))
        assert exc.value.code == 2
        assert "--detach requires --cli" in capsys.readouterr().err

    def test_parser_exposes_detach(self) -> None:
        args = m.build_parser().parse_args(
            ["create", "codespace:cs-1", "do it", "--cli", "--detach"]
        )
        assert args.detach is True and args.cli is True
