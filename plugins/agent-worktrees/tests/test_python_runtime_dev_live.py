from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from runtime_install_fixture import isolate_account_profile, remove_fixture_worktree


@pytest.mark.timeout(2400)
@pytest.mark.parametrize("namespaced", [False, True], ids=["legacy", "namespaced"])
def test_real_native_dev_refreshes_source_and_release_restores_original(
    tmp_path, monkeypatch, namespaced,
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("real dev iteration requires the configured uv executable")
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "dev_live_driver", scripts / "runtime_install.py"
    )
    lifecycle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lifecycle)
    inputs = lifecycle.engine.freeze_inputs(scripts.parent, tmp_path / "worktree")
    plugin, owner = inputs.plugin, tmp_path / "worktree"
    manifest_path = plugin / "pyproject.toml"
    original_project = manifest_path.read_text(encoding="utf-8")
    extra = plugin / "original-only"
    (extra / "runtime_origin_fixture").mkdir(parents=True)
    (extra / "runtime_origin_fixture" / "__init__.py").write_text("", encoding="utf-8")
    (extra / "pyproject.toml").write_text(
        '[project]\nname="runtime-origin-fixture"\nversion="1.0.0"\n'
        '[build-system]\nrequires=["setuptools>=83.0.0"]\n'
        'build-backend="setuptools.build_meta"\n'
        '[tool.setuptools.packages.find]\ninclude=["runtime_origin_fixture"]\n',
        encoding="utf-8",
    )
    manifest_path.write_text(
        original_project + '\n[tool.uv.sources.runtime-origin-fixture]\npath="original-only"\n',
        encoding="utf-8",
    )
    home, result_path = tmp_path / "home", tmp_path / "result.json"
    home.mkdir()
    isolate_account_profile(plugin, home)
    probe_source = plugin / "src" / "agent_worktrees" / "iteration_probe.py"
    probe_source.write_text("VALUE = 1\n", encoding="utf-8")
    git = shutil.which("git")
    assert git is not None
    subprocess.run([git, "-C", str(owner), "init", "-q"], check=True, **no_window_kwargs())
    subprocess.run([git, "-C", str(owner), "add", "."], check=True, **no_window_kwargs())
    subprocess.run(
        [
            git,
            "-C",
            str(owner),
            "-c",
            "user.name=Runtime test",
            "-c",
            "user.email=runtime-test@example.invalid",
            "commit",
            "-qm",
            "Frozen fixture",
        ],
        check=True,
        **no_window_kwargs(),
    )
    root = home / ".agent-worktrees"
    environment = dict(os.environ)
    environment.update(HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT",
        "COPILOT_PLUGIN_ROOT",
        "COPILOT_PLUGIN_STAGED_FROM",
        "AGENT_WORKTREES_PAYLOAD_ROOT",
    ):
        environment.pop(name, None)
    authority = plugin
    if namespaced:
        authority = lifecycle.engine.freeze_inputs(
            plugin, tmp_path / "authorized-payload",
        ).plugin
        spec = importlib.util.spec_from_file_location(
            "dev_live_context",
            authority / "scripts" / "installation-context" / "installation_context.py",
        )
        helper = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = helper
        spec.loader.exec_module(helper)
        durable_home = home / ".copilot-extensions"
        context = helper.stamp_context(
            payload_version=json.loads((authority / "plugin.json").read_text())["version"],
            payload_origin="explicit", expected_namespace_generation=0,
            expected_install_generation=0, payload_root=authority,
            plugin_id="agent-worktrees", durable_home=durable_home,
            source_descriptor={"source": "github", "repo": "ThomasMichon/copilot-extensions"},
            marketplace_key="copilot-extensions", environment=environment,
        )
        (durable_home / "installation-mode.json").write_text(json.dumps({
            "schema": helper.POLICY_SCHEMA, "version": 1,
            "installationMode": {"enabled": True},
        }), encoding="utf-8")
        activation = helper.compare_and_swap_activation(
            context=context["installReceipt"], expected_marketplace_id=context["marketplaceId"],
            expected_plugin_id="agent-worktrees",
            expected_namespace_generation=context["namespaceGeneration"],
            expected_install_generation=context["generation"], expected_activation_generation=0,
            activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": None},
            durable_home=durable_home, legacy_root=root, environment=environment, os_profile=home,
        )
        assert activation["status"] == "ready"
        root = Path(context["pluginRoot"])
        environment.update(
            COPILOT_EXTENSIONS_CONTEXT=context["installReceipt"],
            COPILOT_PLUGIN_STAGED_FROM=str(authority),
            AGENT_WORKTREES_PAYLOAD_ROOT=str(authority),
        )

    def run(argv, label, expected=0):
        log = tmp_path / f"{label}.log"
        with log.open("w", encoding="utf-8") as stream:
            try:
                result = subprocess.run(
                    argv,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=stream,
                    text=True,
                    encoding="utf-8",
                    errors="backslashreplace",
                    # Namespace validation and payload staging precede the
                    # unchanged 480-second context-child budget.
                    timeout=720 if namespaced else 480,
                    **no_window_kwargs(),
                )
            except subprocess.TimeoutExpired as error:
                captured = error.stdout or b""
                if isinstance(captured, bytes):
                    captured = captured.decode("utf-8", errors="backslashreplace")
                pytest.fail(
                    f"{label} did not settle:\n{captured}\n"
                    + log.read_text(encoding="utf-8", errors="backslashreplace")
                )
        assert result.returncode == expected, result.stdout + log.read_text(
            encoding="utf-8", errors="backslashreplace",
        )
        return result.stdout

    run(
        [
            sys.executable,
            "-I",
            "-B",
            str(plugin / "scripts" / "runtime_install.py"),
            "--root",
            str(root),
            "--plugin",
            str(authority),
            "--payload",
            str(authority),
            "--uv",
            uv,
            "--base-python",
            sys.executable,
            "--result",
            str(result_path),
        ],
        "numbered",
    )
    original = json.loads(result_path.read_text(encoding="utf-8"))
    original_manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
    marker_path = root / "versions" / original["runtime_version"] / ".install-complete.json"
    original_marker = marker_path.read_bytes()
    original_python = Path(original["runtime_python"])

    def observe(python):
        output = subprocess.check_output(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "import json; from agent_worktrees.iteration_probe import VALUE; "
                "from agent_worktrees._build_info import BUILD_INFO; "
                "print(json.dumps([VALUE, BUILD_INFO['runtime_generation']]))",
            ],
            env=environment,
            text=True,
            timeout=45,
            **no_window_kwargs(),
        )
        return json.loads(output)

    original_identity = observe(original_python)
    original_origins = json.loads(
        (original_python.parent.parent / ".runtime-input-origins.json").read_text(
            encoding="utf-8",
        ),
    )
    assert "runtime-origin-fixture" in original_origins["packages"]
    manifest_path.write_text(original_project, encoding="utf-8")
    shutil.rmtree(extra)
    if os.name == "nt":
        ps = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        bash_path = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        assert ps and bash_path.is_file(), (
            "Windows mode proof needs PowerShell and Git Bash"
        )

        def ps_command(action, shell):
            return [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(plugin / "scripts" / "install.ps1"),
                action,
                "-InstallDir",
                str(root),
                "-Owner",
                str(owner),
            ]

        first = ps_command("dev", ps)
        second = [
            str(bash_path),
            "-c",
            'exec bash "$1" dev --install-dir "$(cygpath -u "$2")" --owner "$(cygpath -u "$3")"',
            "--",
            (plugin / "scripts" / "install.sh").as_posix(),
            str(root),
            str(owner),
        ]
        if namespaced:
            # Windows receipts use native paths; the POSIX validator is not
            # a Git Bash adapter. Native POSIX namespace coverage runs on CI.
            windows_ps = shutil.which("powershell.exe")
            assert windows_ps
            second = ps_command("dev", windows_ps)
    else:
        bash = shutil.which("bash")
        assert bash
        first = [
            bash,
            str(plugin / "scripts" / "install.sh"),
            "dev",
            "--install-dir",
            str(root),
            "--owner",
            str(owner),
        ]
        second = first
    run(first, "dev-first")
    assert (root / "current-version").read_text().strip() == "dev"
    claim_path = root / "dev-claim.json"
    claim = json.loads(claim_path.read_text())
    assert claim["owner"] == str(owner.resolve())
    assert claim["previous_version"] == original["runtime_version"]
    dev_python = (
        root / "versions" / "dev" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    first_identity = observe(dev_python)
    dev_origins = json.loads(
        (dev_python.parent.parent / ".runtime-input-origins.json").read_text(encoding="utf-8"),
    )
    assert "runtime-origin-fixture" not in dev_origins["packages"]
    assert first_identity[0] == 1 and first_identity[1] != original_identity[1]
    probe_source.write_text("VALUE = 2\n", encoding="utf-8")
    run(second, "dev-second")
    dev_manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
    assert dev_manifest["runtime_slot"] == "dev"
    assert dev_manifest["source"]["path"] == plugin.as_posix()
    assert dev_manifest["source"]["payload_fingerprint"] != (
        original_manifest["source"]["payload_fingerprint"]
    )
    second_identity = observe(dev_python)
    assert second_identity[0] == 2 and second_identity[1] != first_identity[1]
    assert json.loads(claim_path.read_text())["previous_version"] == original["runtime_version"]
    assert marker_path.read_bytes() == original_marker
    assert observe(original_python) == original_identity
    deployed = [str(dev_python), "-I", "-B", "-m", "agent_worktrees"]
    bootstrap = root / "versions" / "dev" / ".runtime-bootstrap"
    assert (bootstrap / "runtime_install_modes.py").is_file()
    # The owning source can disappear; release consumes only retained installed code.
    remove_fixture_worktree(owner)
    assert not owner.exists()
    status = json.loads(run([*deployed, "dev-status", "--json"], "deployed-status"))
    assert status["claim"]["owner"] == str(owner.resolve())
    assert status["runtime_slot"] == "dev"
    release = [*deployed, "dev-release", "--owner", str(owner), "--json"]
    run(
        [*deployed, "dev-release", "--owner", str(tmp_path / "other"), "--json"],
        "deployed-conflict", expected=2,
    )
    assert claim_path.exists() and (root / "current-version").read_text().strip() == "dev"
    receipt_path = original_python.parent.parent / ".runtime-input-origins.json"
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    name = next(iter(receipt["packages"]))
    receipt["packages"][name] = "file:///wrong/origin"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    run(release, "deployed-bad-origins", expected=1)
    assert claim_path.exists() and (root / "current-version").read_text().strip() == "dev"
    receipt_path.write_bytes(receipt_bytes)
    restored_result = json.loads(run(release, "deployed-release"))
    assert restored_result["runtime_version"] == original["runtime_version"]
    assert restored_result["runtime_generation"] == original_identity[1]
    assert not claim_path.exists()
    assert (root / "current-version").read_text().strip() == original["runtime_version"]
    assert (root / "last-known-good").read_text().strip() == original["runtime_version"]
    assert marker_path.read_bytes() == original_marker
    assert observe(original_python) == original_identity
    assert not list((root / ".installer-inputs").glob("mode-owner-*"))
    restored = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
    assert restored["source"] == original_manifest["source"]
    assert restored["runtime_generation"] == original_identity[1]
    assert restored["runtime_slot"] == original["runtime_version"]
