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
from agent_bridge.local_cache_refresh import _checkout_root


@pytest.fixture(autouse=True)
def unmanaged_controller(monkeypatch, tmp_path):
    monkeypatch.setattr("agent_bridge.local_cache_refresh._checkout_root", lambda path: tmp_path)


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
    def test_ssh_ambiguous_registered_remote_refuses_before_native_launch(self, monkeypatch, tmp_path):
        from types import SimpleNamespace
        from agent_bridge import related_plugins

        registry = tmp_path / "repos.yaml"
        registry.write_text(
            "repos:\n  first: {remote: 'https://github.com/example/project.git'}\n"
            "  second: {remote: 'git@github.com:example/project.git'}\n",
        )
        monkeypatch.setattr(related_plugins, "_REPOS_YAML", registry)
        monkeypatch.setattr(related_plugins, "control_plane_anchors", lambda: [tmp_path])
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="example/project")
        monkeypatch.setattr(targeting.subprocess, "run", run)
        with pytest.raises(RuntimeError, match="multiple registered projects"):
            targeting._cmd_create_cli("ssh:host", None, None)
        assert len(calls) == 1 and calls[0][1] == "namespace-target-repo"

    @pytest.mark.parametrize("detach", [False, True])
    @pytest.mark.parametrize("remote", [
        "git@github.com:Example/Project.git", "https://github.com/example/project.git",
        "https://github.com/other/project.git",
        "github.com:example/project.git", "ssh://operator@github.com/example/project.git",
    ])
    def test_ssh_git_remote_selects_machine_only_registered_project(
        self, monkeypatch, tmp_path, detach, remote,
    ):
        from types import SimpleNamespace
        from agent_bridge import related_plugins

        registry = tmp_path / "repos.yaml"
        registry.write_text(f"repos:\n  unique-project-key:\n    remote: {remote}\n")
        monkeypatch.setattr(related_plugins, "_REPOS_YAML", registry)
        related = tmp_path / ".agent-worktrees"
        related.mkdir()
        (related / "related.yaml").write_text(
            "related:\n  unique-project-key:\n"
            "    locus: {preferred: local, machines: [dev6]}\n"
            "    plugins:\n      - only@mkt\n      - {source: disabled@mkt, enable: false}\n"
            "  project:\n    locus: {machines: [dev6]}\n    plugins: [wrong@mkt]\n",
        )
        monkeypatch.setattr(related_plugins, "control_plane_anchors", lambda: [tmp_path])
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="example/project")
        monkeypatch.setattr(targeting.subprocess, "run", run)
        with pytest.raises(SystemExit) as result:
            targeting._cmd_create_cli("ssh:host", None, None, detach=detach)
        assert result.value.code == 0
        launch = calls[1]
        assert launch[3:5] == ["--expected-repository", "example/project"]
        assert ("--stage-plugin" in launch) is ("other/project" not in remote)
        if "other/project" not in remote:
            assert launch[5:9] == ["--stage-plugin", "only@mkt", "--stage-plugin-disabled", "disabled@mkt"]
        assert "wrong@mkt" not in launch

    def test_filesystem_key_is_not_reparsed_as_a_network_host(self, monkeypatch):
        from agent_bridge import related_plugins

        monkeypatch.setattr(
            related_plugins, "_load_yaml", lambda *a: pytest.fail("filesystem key is not a network coordinate"),
        )
        assert related_plugins._registered_project("filesystem:example/project") is None

    @pytest.mark.parametrize("prefix", ["codespace", "container", "ssh"])
    @pytest.mark.parametrize("detach", [False, True])
    def test_related_enablement_reaches_native_provider(self, monkeypatch, prefix, detach):
        from types import SimpleNamespace
        from agent_bridge.transport import PluginRef

        calls = []
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: ["/controller"])
        monkeypatch.setattr(
            "agent_bridge.related_plugins.related_plugins_for_repo",
            lambda repo, anchors=None, **kw: [
                PluginRef("disabled@synthetic", enable=False), PluginRef("enabled@synthetic"),
            ],
        )

        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="example/project")

        monkeypatch.setattr(targeting.subprocess, "run", run)
        with pytest.raises(SystemExit) as result:
            targeting._cmd_create_cli(f"{prefix}:example", None, None, detach=detach)
        assert result.value.code == 0
        assert calls[0][1:] == ["namespace-target-repo", "example"]
        offset = 5 if prefix == "ssh" else 3
        if prefix == "ssh":
            assert calls[1][3:5] == ["--expected-repository", "example/project"]
        assert calls[1][offset:offset + 6] == [
            "--stage-plugin-disabled", "disabled@synthetic",
            "--stage-plugin", "enabled@synthetic", "--plugin-root", "/controller",
        ]
        assert ("--detach" in calls[1]) is detach

    @pytest.mark.parametrize("prefix", ["codespace", "container", "ssh"])
    @pytest.mark.parametrize("configured_anchor", [False, True])
    @pytest.mark.parametrize("detach", [False, True])
    def test_controller_repository_precedes_anchor_for_native_payloads(
        self, monkeypatch, tmp_path, prefix, configured_anchor, detach,
    ):
        import base64
        import io
        import json
        import tarfile
        from pathlib import Path
        from types import SimpleNamespace
        from plugin_resolve.reflection import build_plan

        controller, anchor = tmp_path / "controller", tmp_path / "anchor"
        for root, text, enable in ((controller, "controller-v2", False), (anchor, "anchor-v1", True)):
            payload = root / ".ai" / "sample"
            payload.mkdir(parents=True)
            (payload / "plugin.json").write_text(json.dumps({
                "name": "sample", "version": "1.0.0", "author": {"name": "Synthetic Publisher"},
            }), encoding="utf-8")
            (payload / "payload.txt").write_text(text, encoding="utf-8")
            (root / ".ai" / "marketplace.json").write_text(json.dumps({
                "name": "synthetic", "owner": {"name": "Synthetic Publisher"},
                "plugins": [{"name": "sample", "source": "sample", "version": "1.0.0"}],
            }), encoding="utf-8")
            settings = root / ".github" / "copilot"
            settings.mkdir(parents=True)
            (settings / "settings.json").write_text(json.dumps({
                "extraKnownMarketplaces": {
                    "synthetic": {"source": {"source": "directory", "path": "./.ai"}},
                },
            }), encoding="utf-8")
            related = root / ".agent-worktrees"
            related.mkdir()
            (related / "related.yaml").write_text(
                "related:\n  example/project:\n    plugins:\n"
                f"      - {{source: sample@synthetic, enable: {str(enable).lower()}}}\n",
                encoding="utf-8",
            )
        (anchor / ".git").mkdir()
        (controller / ".git").write_text(f"gitdir: {anchor / '.git' / 'worktrees' / 'controller'}\n")
        nested = controller / "nested" / "directory"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        monkeypatch.setattr("agent_bridge.local_cache_refresh._checkout_root", _checkout_root)
        monkeypatch.setattr(
            "agent_bridge.related_plugins.control_plane_anchors",
            lambda: [anchor] if configured_anchor else [],
        )
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        plans = []

        def run(argv, **kwargs):
            if argv[1] == "namespace-target-repo":
                return SimpleNamespace(returncode=0, stdout="example/project")
            roots = [Path(argv[i + 1]) for i, arg in enumerate(argv) if arg == "--plugin-root"]
            assert roots == [controller, *([anchor] if configured_anchor else [])]
            assert "--stage-plugin" not in argv
            offset = 5 if prefix == "ssh" else 3
            assert argv[offset:offset + 2] == ["--stage-plugin-disabled", "sample@synthetic"]
            assert ("--detach" in argv) is detach
            plan = build_plan([{"source": argv[offset + 1], "enable": False}], roots)
            plans.append(plan)
            item = plan["plugins"][0]
            with tarfile.open(fileobj=io.BytesIO(base64.b64decode(item["archive"]))) as archive:
                assert archive.extractfile(item["relative"] + "/payload.txt").read() == b"controller-v2"
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(targeting.subprocess, "run", run)
        with pytest.raises(SystemExit) as result:
            targeting._cmd_create_cli(f"{prefix}:example", None, None, detach=detach)
        assert result.value.code == 0
        assert len(plans) == 1 and plans[0]["plugins"][0]["enable"] is False

    @pytest.mark.parametrize("prefix", ["codespace", "container"])
    def test_explicit_worktree_is_forwarded_to_the_native_venue_owner(self, monkeypatch, prefix):
        from types import SimpleNamespace
        observed = []
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
        monkeypatch.setattr(
            targeting.subprocess, "run",
            lambda argv, **kw: observed.append(argv) or SimpleNamespace(returncode=0),
        )
        with pytest.raises(SystemExit) as result:
            m._cmd_create(_ns(target=f"{prefix}:synthetic", worktree_id="selected"))
        assert result.value.code == 0
        assert observed[0][3:5] == ["--worktree-id", "selected"]

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
            "--seed",
            f"{targeting._COMPANION_SEED_HEADS_UP}\n\ndo the thing",
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

    def test_ssh_target_execs_agent_ssh_copilot(self, monkeypatch) -> None:
        monkeypatch.setattr(targeting.shutil, "which", lambda name: f"/bin/{name}")
        seen = {}

        class _Result:
            returncode = 0

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return _Result()

        monkeypatch.setattr(targeting.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="ssh:devbox-wsl", prompt="do the thing"))

        assert exc.value.code == 0
        assert seen["argv"] == [
            "/bin/agent-ssh", "copilot", "devbox-wsl",
            "--seed",
            f"{targeting._COMPANION_SEED_HEADS_UP}\n\ndo the thing",
        ]

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
            "--seed", f"{targeting._COMPANION_SEED_HEADS_UP}\n\ngo",
            "--driver", "orchestrator",
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
            "--seed",
            f"{targeting._COMPANION_SEED_HEADS_UP}\n\nmulti\nline\nprompt",
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
        assert seen["kwargs"]["input"] == (
            f"{targeting._COMPANION_SEED_HEADS_UP}\n\n{prompt}"
        )

    def test_detach_without_prompt_sends_no_seed(self, monkeypatch) -> None:
        seen = self._capture(monkeypatch)

        with pytest.raises(SystemExit):
            m._cmd_create(_ns(detach=True))

        assert seen["argv"] == [
            "/bin/agent-codespaces", "copilot", "friendly-eureka", "--detach",
        ]
        assert seen["kwargs"]["input"] is None

    def test_detach_forwards_to_container_targets(self, monkeypatch) -> None:
        seen = self._capture(monkeypatch)
        prompt = "do the container work"

        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="container:repo-1", prompt=prompt, detach=True))

        assert exc.value.code == 0
        assert seen["argv"] == [
            "/bin/agent-containers", "copilot", "repo-1",
            "--detach", "--seed-file", "-",
        ]
        assert seen["kwargs"]["input"] == (
            f"{targeting._COMPANION_SEED_HEADS_UP}\n\n{prompt}"
        )

    def test_detach_forwards_to_ssh_targets(self, monkeypatch) -> None:
        seen = self._capture(monkeypatch)
        prompt = "do the ssh work"

        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(target="ssh:devbox-wsl", prompt=prompt, detach=True))

        assert exc.value.code == 0
        assert seen["argv"] == [
            "/bin/agent-ssh", "copilot", "devbox-wsl",
            "--detach", "--seed-file", "-",
        ]
        assert seen["kwargs"]["input"] == (
            f"{targeting._COMPANION_SEED_HEADS_UP}\n\n{prompt}"
        )

    def test_detach_requires_cli(self, monkeypatch, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            m._cmd_create(_ns(cli=False, detach=True))
        assert exc.value.code == 2
        assert "--detach requires --cli" in capsys.readouterr().err

    def test_parser_exposes_detach(self) -> None:
        args = m.build_parser().parse_args(
            ["create", "ssh:devbox-wsl", "do it", "--cli", "--detach"]
        )
        assert args.detach is True and args.cli is True
