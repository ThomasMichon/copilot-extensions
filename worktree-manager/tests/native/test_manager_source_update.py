"""Real provider and trusted preparation processes for a single acquired selection."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from test_runtime_source import git
from test_runtime_source_update import inventory
from worktree_manager import engine_client as ec
from worktree_manager import runtime_source_config as config
from worktree_manager import self_install, source_config, update_driver_cli, update_events, update_orchestration


@pytest.mark.parametrize("support", ["legacy", "inventory-only", "prepared"])
def test_real_source_suite_preparation_and_provider_skew(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, support: str,
) -> None:
    tmp_path = tmp_path_factory.mktemp("msu")
    supported = support == "prepared"
    root = tmp_path / "manager"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setattr(update_events, "_ut", None)
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for name in ("agent-worktrees", "agent-bridge"):
        payload = repo / "plugins" / name
        payload.mkdir(parents=True)
        (payload / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "1.2.3"\n', encoding="utf-8",
        )
        (payload / "plugin.json").write_text(json.dumps({
            "name": name, "version": "1.2.3",
        }), encoding="utf-8")
        scripts = payload / "scripts"
        scripts.mkdir()
        for extension, body in (
            ("sh", '. "$SCRIPT_DIR/installer-engine.sh"\n'),
            ("ps1", ". (Join-Path $PSScriptRoot 'installer-engine.ps1')\n"),
        ):
            (scripts / f"install.{extension}").write_text(body, encoding="utf-8")
            (scripts / f"installer-engine.{extension}").write_text(
                "# fixture-local installer helper\n", encoding="utf-8",
            )
    tools = repo / "tools"
    tools.mkdir()
    (tools / "prepare_runtime_source.py").write_text(
        "raise RuntimeError('fetched preparation code must never execute')\n", encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "commit", "-qm", "release", "--no-verify")
    git(repo, "switch", "-qc", "dev")
    (repo / "merged.txt").write_text("one exact selection\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "commit", "-qm", "merged development", "--no-verify")
    selected = git(repo, "rev-parse", "HEAD")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    git(repo, "switch", "-q", "main")
    config.set_source(config.RuntimeSource("dev", str(repo), allow_dev=True))
    source_config.set_source(repo="https://example.invalid/manager.git", ref="independent")
    policy_before = (root / "config.toml").read_bytes()
    receipt = tmp_path / "engine-receipt.json"
    document = inventory("agent-worktrees", "agent-bridge")
    if not supported:
        document.pop("runtime_sources")
    provider = tmp_path / "provider.py"
    participant = tmp_path / "participant.txt"
    provider.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"inventory = {document!r}\n"
        "if '--inventory' in sys.argv:\n"
        f"    if {support == 'legacy'!r}:\n"
        "        sys.stderr.write('unknown argument: --inventory\\n')\n"
        "        sys.exit(2)\n"
        "    print(json.dumps(inventory))\n"
        "else:\n"
        "    from update_driver import borrowed_custody\n"
        "    with borrowed_custody() as custody:\n"
        f"        Path({str(participant)!r}).write_text(custody.request_id)\n"
        "    assert sys.argv[1:3] == ['update', '--no-manager']\n"
        "    packet = json.loads(sys.argv[sys.argv.index('--runtime-sources') + 1])\n"
        "    for name, item in packet['sources'].items():\n"
        "        identity = json.loads((Path(item['payload']) / 'runtime-source.json').read_text())\n"
        "        assert identity == item['identity']['runtime_source']\n"
        f"    Path({str(receipt)!r}).write_text(json.dumps(packet))\n",
        encoding="utf-8",
    )
    command = [sys.executable, "-I", "-B", str(provider)]
    monkeypatch.setattr(ec, "_ENGINE_CMD_OVERRIDE", command)
    manager_calls = []

    def self_update(**kwargs: object) -> self_install.SelfUpdateResult:
        manager_calls.append(kwargs)
        # A self-update cannot redirect the capability-qualified provider.
        monkeypatch.setattr(ec, "_ENGINE_CMD_OVERRIDE", ["must-not-execute"])
        return self_install.SelfUpdateResult(action="already-current", version="1")

    monkeypatch.setattr(self_install, "self_update", self_update)
    if supported:
        import update_driver

        manager_receipt = tmp_path / "manager-self-update.txt"
        runner = tmp_path / "manager-runner.py"
        runner.write_text(
            "import sys\nfrom pathlib import Path\n"
            "from worktree_manager import self_install, engine_client, __main__ as manager\n"
            "def self_update(**kwargs):\n"
            f"    Path({str(manager_receipt)!r}).write_text('independent source')\n"
            "    engine_client.set_engine_command(['must-not-execute'])\n"
            "    return self_install.SelfUpdateResult(action='already-current', version='1')\n"
            "self_install.self_update = self_update\n"
            "sys.exit(manager.main(['update']))\n",
            encoding="utf-8",
        )
        monkeypatch.setenv(ec.ENGINE_ARGV_ENV, json.dumps(command))
        context = tuple(update_driver_cli.request_context().items())
        spec = update_driver.RequestSpec(
            backend="manager", cwd=str(tmp_path), source=update_driver_cli._source_key(),
            context=context,
        )
        queue = tmp_path / ".copilot-extensions/update-driver"
        request = update_driver.enqueue(
            spec, root=queue, runner=update_driver.TrustedRunner(
                (sys.executable, "-I", "-B", str(runner)), str(tmp_path),
                timeout=60,
                context_keys=update_driver_cli._CONTEXT_KEYS,
            ),
        )
        assert update_driver.drain(root=queue) == 0
        settled = update_driver.status(request.id, root=queue)
        assert settled.state == "completed" and settled.exit_code == 0, settled.log_path.read_text()
        assert len(update_driver.status(root=queue)) == 1
        assert participant.read_text() == request.id
        assert manager_receipt.read_text() == "independent source"
    else:
        run = update_events.UpdateRun()
        code = update_orchestration.run_update_steps([], run, version="1")
        assert run.finish(code) == 1
    assert (root / "config.toml").read_bytes() == policy_before
    assert manager_calls == []
    if not supported:
        assert not (root / "runtime-sources").exists()
        assert not receipt.exists()
        return
    packet = json.loads(receipt.read_text())
    assert set(packet["sources"]) == {"agent-worktrees", "agent-bridge"}
    for name, record in packet["sources"].items():
        identity = record["identity"]["runtime_source"]
        assert identity["source_commit"] == selected and identity["source_tree"] == tree
        payload = Path(record["payload"])
        assert payload.parent == root / "runtime-sources/prepared" / name
        assert payload.is_dir()
    checkout = next((root / "runtime-sources").glob("*/checkout"))
    assert git(checkout, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
    assert git(checkout, "status", "--porcelain=v1", "--untracked-files=all", "--ignored") == ""
