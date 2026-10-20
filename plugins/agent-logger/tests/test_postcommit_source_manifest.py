"""Postcommit manifests use admitted snapshots, not a changing publisher."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pytest

from . import test_source_runtime_reselection as native
from .test_source_runtime_reselection import _reclaim_case_runtimes  # noqa: F401


def _stage_with_postcommit_mutation(stage):
    def prepare(tmp_path, plugin):
        payload, fixtures = stage(tmp_path, plugin)
        entry = payload / "scripts" / "versioned_runtime.py"
        original = 'if __name__ == "__main__":\n    raise SystemExit(main())'
        replacement = '''
if __name__ == "__main__":
    code = main()
    if code == 0 and "publish-redirects" in sys.argv and os.environ.get("TEST_MUTATE_PUBLISHER_AFTER_REDIRECT") == "1":
        publisher = Path(__file__).resolve().parent.parent
        receipt = publisher / "runtime-source.json"
        data = json.loads(receipt.read_text(encoding="utf-8"))
        data["source_tree"] = "d" * 40
        receipt.write_text(json.dumps(data), encoding="utf-8")
        (publisher / "pyproject.toml").write_text('[project]\\nname = "changed-publisher"\\nversion = "8.8.8"\\n', encoding="utf-8")
        print("PUBLISHER_CHANGED_AFTER_REDIRECT_COMMIT", file=sys.stderr)
    raise SystemExit(code)
'''.strip()
        body = entry.read_text(encoding="utf-8")
        assert original in body
        entry.write_text(body.replace(original, replacement, 1), encoding="utf-8", newline="\n")
        return payload, fixtures
    return prepare


@pytest.mark.parametrize("plugin", ["agent-logger", "agent-vault"])
@pytest.mark.parametrize("fresh", [False, True], ids=["retained", "fresh"])
@pytest.mark.parametrize("host", ["pwsh", "powershell"] if os.name == "nt" else [None])
def test_origin_changes_after_commit_still_publish_actual_new_manifest(tmp_path, monkeypatch, plugin, fresh, host):
    if host:
        executable = shutil.which(host)
        if not executable:
            pytest.skip(f"{host} unavailable")
        monkeypatch.setattr(native, "_host", lambda: executable)
    monkeypatch.setattr(native, "_small_stage", _stage_with_postcommit_mutation(native._small_stage))
    state = native._case(tmp_path, plugin, incomplete_first=fresh)
    baseline = native._reuse(state, state.second)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    before = (state.root / "deploy-manifest.json").read_bytes()
    expected_hash = native._payload_hash(state)
    state.env["TEST_MUTATE_PUBLISHER_AFTER_REDIRECT"] = "1"
    installed = native._reuse(state, state.first, build_candidate=fresh)
    assert json.loads((state.payload / "runtime-source.json").read_text())["source_tree"] == "d" * 40
    assert (state.payload / "pyproject.toml").read_text().find("8.8.8") >= 0
    assert installed.returncode == 0, installed.stdout + installed.stderr
    native._assert_manifest_and_helpers(state, state.first)
    native._assert_completion_unchanged(state)
    raw = (state.root / "deploy-manifest.json").read_bytes()
    assert raw != before
    manifest = json.loads(raw.decode("utf-8-sig"))
    assert manifest["source"]["version"] == state.first.package_version
    assert manifest["source"]["content_hash"] == expected_hash
    assert Path(manifest["source"]["path"]).resolve() == state.payload.resolve()
    assert manifest["source"]["kind"] == "local"
    assert manifest["source"]["commit"] == "fixture"
    selected_snapshot = Path((state.root / "payload-dir").read_text().strip())
    assert selected_snapshot != state.payload
    assert json.loads((selected_snapshot / "runtime-source.json").read_text()) == state.first.document()
    assert (state.root / "current-version").read_text().strip() == state.first.runtime_version
