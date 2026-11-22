"""Verify that the finite onboarding entry consumes the persisted checkout."""
from pathlib import Path
import json
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest

from worktree_manager import harness_onboarding as onboarding


def check_registered_checkout_readback(tmp_path: Path, case: unittest.TestCase) -> None:
    root = tmp_path / "footprint"
    src = tmp_path / "src"
    harness, companion = src / "control", src / "records"
    for path in (harness, companion):
        path.mkdir(parents=True)
        onboarding.boot._git(path, "init", "--quiet")
        onboarding.boot._git(path, "remote", "add", "origin", f"https://github.com/example/{path.name}")
    onboarding.boot.first_registration(root, src, str(harness))
    remote = "example/records"
    no_write = ModuleType("nonpersisting_writer")
    no_write.write_binding = lambda *args, **kwargs: None
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
    mixed = json.loads(overlay.read_bytes())
    mixed["_agentWorktreesToolPluginOverlay"] = {"project": "foreign"}
    overlay.write_text(json.dumps(mixed), encoding="utf-8")
    before = overlay.read_bytes(), overlay.stat().st_mtime_ns
    with case.assertRaisesRegex(RuntimeError, "registered Git family"):
        onboarding.compose_checkout(harness, companion)
    assert (overlay.read_bytes(), overlay.stat().st_mtime_ns) == before


class BindingTest(unittest.TestCase):
    def test_registered_checkout_readback(self) -> None:
        with TemporaryDirectory() as directory:
            check_registered_checkout_readback(Path(directory), self)


if __name__ == "__main__":
    unittest.main()
