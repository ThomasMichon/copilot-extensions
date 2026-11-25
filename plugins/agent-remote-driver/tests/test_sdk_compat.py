"""Exercise the shared extension adapter without a live CLI session."""

from importlib.resources import files
import json
from pathlib import Path
import shutil
import subprocess

from agent_procutil import no_window_kwargs
from agent_remote_driver.headed.staging import stage_extension
import pytest


def test_extension_sdk_compatibility() -> None:
    node = shutil.which("node")
    assert node, "extension SDK compatibility controls require Node"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("sdk-compat.test.mjs"))],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_headed_payload_retains_and_verifies_the_shared_sdk_helper(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    selected = stage_extension(attempt)
    helper = selected / "extensions" / "copilot-driver-headed" / "sdk-compat.mjs"
    assert helper.read_bytes() == files("agent_remote_driver.bridge_extension").joinpath(
        "sdk-compat.mjs"
    ).read_bytes()
    helper.write_text("changed retained helper", encoding="utf-8")
    with pytest.raises(ValueError, match="assets changed"):
        stage_extension(attempt)
    assert helper.read_text() == "changed retained helper"


def test_selected_connector_stages_one_entrypoint_with_a_complete_immutable_core(tmp_path: Path) -> None:
    from agent_remote_driver.bridge_extension import extension_sources

    attempt = tmp_path / "attempt"
    legacy = stage_extension(attempt)
    selected = stage_extension(attempt, native_connector=True)
    assert selected != legacy
    assert stage_extension(attempt) == legacy
    assert stage_extension(attempt, native_connector=True) == selected
    root = selected / "extensions" / "copilot-driver-headed"
    assert (root / "extension.mjs").read_bytes() == files("agent_remote_driver.headed").joinpath(
        "connector-extension.mjs",
    ).read_bytes()
    assert {(path.relative_to(root / "core").as_posix(), path.read_bytes())
            for path in (root / "core").iterdir()} == set(extension_sources())
    implementation = root / "core" / "native-connector.mjs"
    implementation.write_text("changed selected core", encoding="utf-8")
    with pytest.raises(ValueError, match="assets changed"):
        stage_extension(attempt, native_connector=True)
    assert implementation.read_text() == "changed selected core"


@pytest.mark.parametrize("mode", ["selected", "wrong-loadout"])
def test_staged_connector_and_actual_support_facades_join_once_without_suite_services(tmp_path: Path, mode: str) -> None:
    from agent_procutil.owned_process import run_caller_owned
    from agent_remote_driver.bridge_extension import extension_sources

    root = Path(__file__).resolve().parents[3]
    selected = stage_extension(tmp_path / "attempt", native_connector=True)
    roots = {}
    for name in ("agent-bridge", "agent-remote-driver"):
        plugin = tmp_path / "plugins" / name
        extension = plugin / "extensions" / name
        extension.mkdir(parents=True)
        shutil.copyfile(root / "plugins" / name / "extensions" / name / "extension.mjs",
                        extension / "extension.mjs")
        resources = extension / "core" if name == "agent-bridge" else extension
        resources.mkdir(exist_ok=True)
        for asset, body in extension_sources():
            if name == "agent-bridge" or asset in {"sdk-compat.mjs", "native-connector.mjs"}:
                (resources / asset).write_bytes(body)
        roots[name] = plugin
    sdk = tmp_path / "node_modules" / "@microsoft" / "copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({
        "type": "module", "exports": {".": "./index.mjs", "./extension": "./extension.mjs"},
    }), encoding="utf-8")
    (sdk / "index.mjs").write_text("export const approveAll = () => { throw Error('forbidden'); };",
                                  encoding="utf-8")
    (sdk / "extension.mjs").write_text("export const joinSession = (...args) => globalThis.__joinSession(...args);",
                                      encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    node = shutil.which("node")
    assert node, "staged connector controls require Node"
    result = run_caller_owned([
        node, str(Path(__file__).parent / "fixtures" / "native-connector-entry.mjs"),
        str(selected), str(roots["agent-bridge"]), str(roots["agent-remote-driver"]), str(config), mode,
    ], cwd=tmp_path, timeout=15, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "wrong loadout refused before native input" if mode == "wrong-loadout" else "one staged Driver join"
    assert expected in result.stdout
