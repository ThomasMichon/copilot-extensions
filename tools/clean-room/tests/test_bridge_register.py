from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "bridge_register.py"


def _bash_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return resolved.as_posix()
    relative = resolved.relative_to(resolved.anchor).as_posix()
    return f"/mnt/{resolved.drive[0].lower()}/{relative}"


def _fake_docker_path(tmp_path: Path) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = inspect ]; then printf 'id-%s true\\n' \"$4\"; exit 0; fi\n"
        "if [ \"$1\" = exec ]; then [ \"$5\" != -- ]; exit $?; fi\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    docker.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def _run(
    tmp_path: Path,
    *arguments: str,
    path: str | None = None,
    script: Path = _SCRIPT,
) -> subprocess.CompletedProcess[str]:
    providers_dir = tmp_path / "providers.d"
    env = {
        **os.environ,
        "AGENT_BRIDGE_PROVIDERS_DIR": str(providers_dir),
    }
    if path is not None:
        env["PATH"] = path
        docker = tmp_path / "bin" / "docker"
        if docker.is_file():
            env["CLEAN_ROOM_DOCKER_COMMAND_JSON"] = json.dumps(
                ["bash", _bash_path(docker)]
            )
    return subprocess.run(
        [
            sys.executable,
            str(script),
            *arguments,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _register(
    tmp_path: Path,
    container: str = "cr-test",
    *options: str,
    acp_command: str = "copilot --acp",
    path: str | None = None,
) -> dict[str, object]:
    path = path or _fake_docker_path(tmp_path)
    result = _run(
        tmp_path,
        "--acp-command",
        acp_command,
        *options,
        "register",
        "--container",
        container,
        path=path,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(
        (tmp_path / "providers.d" / "cleanroom.json").read_text(encoding="utf-8")
    )


def test_register_omits_empty_acp_cwd_from_manifest_command(tmp_path: Path):
    manifest = _register(tmp_path)
    command = manifest["command"]

    assert isinstance(command, list)
    assert all(isinstance(value, str) and value for value in command)
    assert "--acp-cwd" not in command
    assert command[1] == str(tmp_path / "providers" / "cleanroom-provider.py")

    _register(tmp_path, "cr-cwd", "--acp-cwd", "/workspace")
    registration = json.loads(
        (tmp_path / "cleanroom.d" / "cr-cwd.json").read_text(encoding="utf-8")
    )
    assert registration["acp_cwd"] == "/workspace"


@pytest.mark.parametrize("cwd", ["relative/path", "/bad\npath", "/bad\tpath"])
def test_register_rejects_invalid_acp_cwd(tmp_path: Path, cwd: str):
    result = _run(
        tmp_path,
        "--acp-command",
        "copilot --acp",
        "--acp-cwd",
        cwd,
        "register",
        "--container",
        "cr-test",
    )

    assert result.returncode == 2
    assert not (tmp_path / "providers.d" / "cleanroom.json").exists()


def test_concurrent_registrations_resolve_their_own_launch_settings(tmp_path: Path):
    path = _fake_docker_path(tmp_path)

    manifest = _register(
        tmp_path,
        "cr-one",
        "--acp-cwd",
        "/worktree/one",
        acp_command="copilot --acp --plugin-dir /plugins/one",
        path=path,
    )
    _register(
        tmp_path,
        "cr-two",
        "--acp-cwd",
        "/worktree/two",
        acp_command="copilot --acp --plugin-dir /plugins/two",
        path=path,
    )

    provider_script = Path(manifest["command"][1])
    first = _run(
        tmp_path,
        "namespace-resolve",
        "cr-one",
        path=path,
        script=provider_script,
    )
    second = _run(
        tmp_path,
        "namespace-resolve",
        "cr-two",
        path=path,
        script=provider_script,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_spec = json.loads(first.stdout)
    second_spec = json.loads(second.stdout)
    assert first_spec["workspace_folder"] == "/worktree/one"
    assert second_spec["workspace_folder"] == "/worktree/two"
    docker_command = ["bash", _bash_path(tmp_path / "bin" / "docker")]
    assert first_spec["spawn_command"][:2] == docker_command
    assert second_spec["spawn_command"][:2] == docker_command
    assert first_spec["spawn_command"][2:5] == ["exec", "-i", "id-cr-one"]
    assert second_spec["spawn_command"][2:5] == ["exec", "-i", "id-cr-two"]
    assert first_spec["spawn_command"][-1].endswith("/plugins/one")
    assert second_spec["spawn_command"][-1].endswith("/plugins/two")
    assert provider_script.is_file()


def test_stale_registration_does_not_attach_to_replacement_container(tmp_path: Path):
    path = _fake_docker_path(tmp_path)
    _register(tmp_path, "cr-reused", "--acp-cwd", "/worktree/old", path=path)

    docker = tmp_path / "bin" / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = inspect ]; then printf 'replacement-%s true\\n' \"$4\"; exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest = json.loads(
        (tmp_path / "providers.d" / "cleanroom.json").read_text(encoding="utf-8")
    )
    result = _run(
        tmp_path,
        "namespace-resolve",
        "cr-reused",
        path=path,
        script=Path(manifest["command"][1]),
    )

    assert result.returncode == 3
    assert result.stdout == ""


def test_stopped_registered_container_is_not_available(tmp_path: Path):
    path = _fake_docker_path(tmp_path)
    _register(tmp_path, "cr-stopped", path=path)

    docker = tmp_path / "bin" / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = inspect ]; then printf 'id-%s false\\n' \"$4\"; exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest = json.loads(
        (tmp_path / "providers.d" / "cleanroom.json").read_text(encoding="utf-8")
    )
    provider_script = Path(manifest["command"][1])

    resolved = _run(
        tmp_path,
        "namespace-resolve",
        "cr-stopped",
        path=path,
        script=provider_script,
    )
    ready = _run(
        tmp_path,
        "namespace-ensure-ready",
        "cr-stopped",
        path=path,
        script=provider_script,
    )

    assert resolved.returncode == 3
    assert ready.returncode == 1


def test_missing_registered_cwd_is_not_available(tmp_path: Path):
    path = _fake_docker_path(tmp_path)
    _register(tmp_path, "cr-missing-cwd", "--acp-cwd", "/worktree/gone", path=path)

    docker = tmp_path / "bin" / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = inspect ]; then printf 'id-%s true\\n' \"$4\"; exit 0; fi\n"
        "if [ \"$1\" = exec ]; then exit 1; fi\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest = json.loads(
        (tmp_path / "providers.d" / "cleanroom.json").read_text(encoding="utf-8")
    )
    provider_script = Path(manifest["command"][1])

    resolved = _run(
        tmp_path,
        "namespace-resolve",
        "cr-missing-cwd",
        path=path,
        script=provider_script,
    )
    ready = _run(
        tmp_path,
        "namespace-ensure-ready",
        "cr-missing-cwd",
        path=path,
        script=provider_script,
    )

    assert resolved.returncode == 3
    assert ready.returncode == 1


def test_unregister_does_not_delete_replacement_registration(tmp_path: Path):
    path = _fake_docker_path(tmp_path)
    _register(tmp_path, "cr-reused", path=path)
    old_id = "id-cr-reused"

    docker = tmp_path / "bin" / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = inspect ]; then printf 'new-%s true\\n' \"$4\"; exit 0; fi\n"
        "if [ \"$1\" = exec ]; then exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    _register(tmp_path, "cr-reused", path=path)
    registration_path = tmp_path / "cleanroom.d" / "cr-reused.json"

    result = _run(
        tmp_path,
        "unregister",
        "--container",
        "cr-reused",
        "--container-id",
        old_id,
        path=path,
    )

    assert result.returncode == 2
    assert json.loads(registration_path.read_text(encoding="utf-8"))[
        "container_id"
    ] == "new-cr-reused"

    stale = _run(
        tmp_path,
        "unregister",
        "--container",
        "cr-reused",
        "--stale",
        path=path,
    )
    assert stale.returncode == 2
    assert registration_path.is_file()


def test_stale_unregister_requires_recorded_container_to_be_gone(tmp_path: Path):
    path = _fake_docker_path(tmp_path)
    _register(tmp_path, "cr-stale", path=path)
    registration_path = tmp_path / "cleanroom.d" / "cr-stale.json"

    docker = tmp_path / "bin" / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'Error: No such object: %s\\n' \"$4\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    result = _run(
        tmp_path,
        "unregister",
        "--container",
        "cr-stale",
        "--stale",
        path=path,
    )

    assert result.returncode == 0, result.stderr
    assert not registration_path.exists()


def test_stale_unregister_fails_closed_when_docker_is_unavailable(tmp_path: Path):
    path = _fake_docker_path(tmp_path)
    _register(tmp_path, "cr-unknown", path=path)
    registration_path = tmp_path / "cleanroom.d" / "cr-unknown.json"

    docker = tmp_path / "bin" / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'Cannot connect to the Docker daemon\\n' >&2\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    result = _run(
        tmp_path,
        "unregister",
        "--container",
        "cr-unknown",
        "--stale",
        path=path,
    )

    assert result.returncode == 2
    assert registration_path.is_file()


@pytest.mark.parametrize(
    "subcommand",
    ["namespace-resolve", "namespace-target-repo", "namespace-ensure-ready"],
)
def test_namespace_commands_reject_container_name_traversal(
    tmp_path: Path,
    subcommand: str,
):
    outside = tmp_path / "escape.json"
    outside.write_text('{"sentinel": true}\n', encoding="utf-8")

    result = _run(tmp_path, subcommand, "../escape")

    assert result.returncode == 2
    assert outside.read_text(encoding="utf-8") == '{"sentinel": true}\n'
