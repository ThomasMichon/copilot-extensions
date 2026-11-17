"""Exercise generated native fixture imports without a native CLI or session."""

import importlib
import json
from pathlib import Path
import shutil

from agent_procutil.owned_process import run_caller_owned
import pytest


@pytest.mark.parametrize("namespace", ["@microsoft", "@github"])
def test_generated_native_fixture_supports_namespace_only_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, namespace: str,
) -> None:
    node = shutil.which("node")
    assert node, "generated SDK fixture controls require Node"
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "native"))
    fixture_owner = importlib.import_module("test_headed_extension")
    entrypoint = tmp_path / "extension.mjs"
    entrypoint.write_text(
        fixture_owner._native_sdk_import() + "console.log(await joinSession());\n",
        encoding="utf-8",
    )
    sdk = tmp_path / "node_modules" / namespace / "copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({
        "type": "module", "exports": {".": "./root.mjs", "./extension": "./extension.mjs"},
    }), encoding="utf-8")
    (sdk / "root.mjs").write_text("export {};\n", encoding="utf-8")
    (sdk / "extension.mjs").write_text(
        f"export async function joinSession() {{ return {json.dumps(namespace)}; }}\n",
        encoding="utf-8",
    )
    result = run_caller_owned(
        [node, str(entrypoint)], cwd=tmp_path, timeout=15,
        max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == namespace
