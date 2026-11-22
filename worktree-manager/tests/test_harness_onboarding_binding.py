"""Verify that the finite onboarding entry consumes the persisted checkout."""
from pathlib import Path
import json
import os
import subprocess
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch

from worktree_manager import harness_onboarding as onboarding
from worktree_manager import bootstrap_harness


class NonpersistingWriter(ModuleType):
    def write_binding(self, *args: object, **kwargs: object) -> None:
        pass


def check_registered_checkout_readback(tmp_path: Path, case: unittest.TestCase) -> None:
    root = tmp_path / "footprint"
    src = tmp_path / "src"
    harness, companion = src / "control", src / "records"
    for path in (harness, companion):
        path.mkdir(parents=True)
        bootstrap_harness._git(path, "init", "--quiet")
        bootstrap_harness._git(path, "remote", "add", "origin", f"https://github.com/example/{path.name}")
    bootstrap_harness.first_registration(root, src, str(harness))
    remote = "example/records"
    no_write = NonpersistingWriter("nonpersisting_writer")
    with case.assertRaisesRegex(ValueError, "read back"):
        onboarding.bind_companion(root, harness.name, remote, companion, no_write)

    kit = Path(onboarding.__file__).resolve().parent / "_runtime_assets/scripts/runtime-source-tools"
    writer = onboarding._asset(kit / "plugins/harness-knowledge/skills/binding-knowledge/scripts", "binding_yaml")
    assert onboarding.bind_companion(root, harness.name, remote, companion, writer).samefile(companion)
    config = root / "repos" / harness.name / "config.yaml"
    baseline = config.read_bytes(), config.stat().st_mtime_ns
    assert onboarding.bind_companion(root, harness.name, remote, companion, writer).samefile(companion)
    assert (config.read_bytes(), config.stat().st_mtime_ns) == baseline

    for repo, app in ((harness, "agent-bridge"), (companion, "agent-dispatch")):
        settings = repo / ".github" / "copilot" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"enabledPlugins": {app + "@copilot-extensions": True}}), encoding="utf-8")
    onboarding.compose_checkout(harness, companion)
    overlay = harness / ".github" / "copilot" / "settings.local.json"
    assert json.loads(overlay.read_bytes())["enabledPlugins"]["agent-dispatch@copilot-extensions"] is True
    before = overlay.read_bytes(), overlay.stat().st_mtime_ns
    onboarding.compose_checkout(harness, companion)
    assert (overlay.read_bytes(), overlay.stat().st_mtime_ns) == before
    settings = harness / ".copilot-extensions/agent-worktrees/config.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text("compose_knowledge_plugins: false\n", encoding="utf-8")
    enabled = onboarding.composition_enabled(harness, config, harness.name)
    assert not enabled
    current = json.loads(overlay.read_bytes())
    current["enabledPlugins"]["operator@example"] = False
    overlay.write_text(json.dumps(current), encoding="utf-8")
    onboarding.compose_checkout(harness, companion, enabled=enabled)
    assert json.loads(overlay.read_bytes())["enabledPlugins"] == {"operator@example": False}
    assert onboarding.harness_state._read_yaml(config, strict=True)["knowledge_repo"] == companion.name
    onboarding.compose_checkout(harness, companion)
    mixed = json.loads(overlay.read_bytes())
    mixed["_agentWorktreesToolPluginOverlay"] = {"project": "foreign"}
    overlay.write_text(json.dumps(mixed), encoding="utf-8")
    before = overlay.read_bytes(), overlay.stat().st_mtime_ns
    with case.assertRaisesRegex(RuntimeError, "registered Git family"):
        onboarding.compose_checkout(harness, companion)
    assert (overlay.read_bytes(), overlay.stat().st_mtime_ns) == before


class BindingTest(unittest.TestCase):
    def test_setup_has_closed_inputs_and_no_release_credentials(self) -> None:
        keys = ("GH_TOKEN", "GITHUB_TOKEN", "ARTIFACT_VERIFY_TOKEN")
        with patch.dict(os.environ, {k: "fixture" for k in keys}, clear=True), patch.object(
            subprocess, "run", return_value=subprocess.CompletedProcess([], 7),
        ) as child:
            self.assertEqual(onboarding.install_prepared(["--stage"]), 7)
            env = child.call_args.kwargs["env"]
            self.assertTrue(set(keys).isdisjoint(env))
            self.assertEqual(env["UV_OFFLINE"], "1")
            self.assertEqual(env["UV_PYTHON_DOWNLOADS"], "never")
            self.assertTrue(all(os.environ[k] == "fixture" for k in keys))

    def test_registered_checkout_readback(self) -> None:
        with TemporaryDirectory() as directory:
            check_registered_checkout_readback(Path(directory), self)


if __name__ == "__main__":
    unittest.main()
