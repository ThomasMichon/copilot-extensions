"""Host-safe contracts for the explicitly opt-in Komodo proof runner."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "clean-room" / "komodo-role-fleet" / "run.py"
SPEC = importlib.util.spec_from_file_location("komodo_role_proof", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_proof_requires_explicit_privileged_admission(tmp_path):
    with patch("sys.argv", ["run.py", "--results-root", str(tmp_path)]):
        try:
            MODULE.main()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("privileged proof must not start implicitly")
    assert not list(tmp_path.iterdir())


def test_results_root_indirection_is_refused_before_external_operations(tmp_path):
    with patch("sys.argv", [
        "run.py", "--allow-privileged-dind", "--results-root", str(tmp_path),
    ]), patch.object(Path, "is_symlink", return_value=True), patch.object(MODULE, "run") as run:
        try:
            MODULE.main()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("symlink indirection must be refused")
    run.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_host_safe_contracts_are_collected_in_required_ci():
    workflow = (MODULE_PATH.parents[3] / ".github" / "workflows" / "ci.yml").read_text()
    assert "tools/tests/test_komodo_role_fleet.py" in workflow


def test_first_command_failure_still_cleans_all_profiles_and_secret_files(tmp_path):
    calls = []

    def failing_command(argv, *, cwd, data=None):
        calls.append(argv)
        if "down" in argv:
            return b""
        if "info" in argv:
            return b"linux"
        if "pull" in argv:
            return b""
        raise MODULE.ProofError("synthetic engine unavailable")

    with patch.object(MODULE, "command", failing_command):
        receipt = MODULE.run(tmp_path)
    assert receipt["result"] == "fail"
    assert receipt["cleanup"] == "complete"
    assert receipt["error"] == "synthetic engine unavailable"
    teardown = calls[-1]
    assert teardown[-5:] == ["--profile", "*", "down", "-v", "--remove-orphans"]
    assert "--profile" in teardown and "*" in teardown
    assert not list(tmp_path.glob("*.secret"))
    assert not (tmp_path / "enrolled.toml").exists()
    assert json.loads((tmp_path / "receipt.json").read_text()) == receipt


def test_teardown_failure_is_not_reported_as_success(tmp_path):
    def fail(argv, **kwargs):
        if "info" in argv:
            return b"linux"
        if "pull" in argv:
            return b""
        raise MODULE.ProofError("operation failed")

    with patch.object(MODULE, "command", fail):
        receipt = MODULE.run(tmp_path)
    assert receipt["cleanup"] == "failed"
    assert receipt["result"] == "fail"
    assert not list(tmp_path.glob("*.secret"))


def test_partial_secret_setup_is_inside_cleanup_boundary(tmp_path):
    original = Path.write_text

    def write(path, data, **kwargs):
        if path.name == "admin.secret":
            raise OSError("synthetic secret setup failure")
        return original(path, data, **kwargs)

    with patch.object(Path, "write_text", write), patch.object(MODULE, "command") as external:
        receipt = MODULE.run(tmp_path)
    external.assert_not_called()
    assert receipt["result"] == "fail"
    assert receipt["cleanup"] == "complete"
    assert receipt["error_type"] == "OSError"
    assert not list(tmp_path.glob("*.secret"))
    assert (tmp_path / "receipt.json").exists()


def test_fixture_has_no_host_socket_mount_or_published_ports():
    config = (MODULE.FIXTURE / "compose.yaml").read_text()
    assert "/var/run/docker.sock:" not in config
    assert "ports:" not in config
    assert "internal: true" in config
    assert "nested-socket:/var/run" in config
    assert "privileged: true" in config
    assert "restart: always" not in config
