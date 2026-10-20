from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomllib
from agent_procutil import no_window_kwargs


@pytest.fixture
def modes(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    for name in ("runtime_install", "runtime_install_modes"):
        spec = importlib.util.spec_from_file_location(name, scripts / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return sys.modules["runtime_install_modes"]


def test_dev_bootstrap_attributes_worktree_inputs_not_authority_payload(
    modes, tmp_path, monkeypatch,
):
    owner = tmp_path / "owner"
    plugin = owner / "plugins" / "agent-worktrees"
    plugin.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="1.0.0"\n', encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(owner), "init", "-q"], check=True, **no_window_kwargs(),
    )
    authority = tmp_path / "authorized-payload"
    authority.mkdir()
    root = tmp_path / "runtime"
    provenance = {"version": "1.0.0", "source": str(plugin),
                  "deployment_source": json.dumps({"path": plugin.as_posix()})}

    @contextmanager
    def held(**_kwargs):
        yield

    monkeypatch.setattr(
        modes.lifecycle, "Governance",
        lambda requested, payload: SimpleNamespace(
            root=requested, payload=payload, held=held, signature=lambda: {"generation": 1},
        ),
    )

    def source_provenance(source, version, identity):
        assert source == plugin and version == "1.0.0"
        assert identity is None
        return provenance

    @contextmanager
    def candidate(control_root, version, **kwargs):
        assert version == "control" and kwargs["plugin"].is_relative_to(control_root)
        assert (kwargs["plugin"] / "pyproject.toml").read_bytes() == (plugin / "pyproject.toml").read_bytes()
        assert kwargs["construction_data"] == provenance
        assert kwargs["construction_data"]["source"] == str(plugin)
        assert json.loads(kwargs["construction_data"]["deployment_source"])["path"] == authority.as_posix()
        yield SimpleNamespace(frozen_plugin=control_root / "frozen")

    class OwnerProcess:
        returncode = 0

        def __init__(self, argv, **_kwargs):
            assert argv[argv.index("--payload") + 1] == str(authority)
            assert argv[argv.index("--owner") + 1] == str(owner.resolve())
            Path(argv[argv.index("--result") + 1]).write_text('{"runtime_version":"dev"}')

        def wait(self):
            return self.returncode

    monkeypatch.setattr(modes.lifecycle, "_source_provenance", source_provenance)
    monkeypatch.setattr(modes.lifecycle.engine, "numbered_candidate", candidate)
    monkeypatch.setattr(
        modes.lifecycle.runtime, "slot_python", lambda *_args: Path(sys.executable),
    )
    monkeypatch.setattr(modes.subprocess, "Popen", OwnerProcess)
    # Git resolves the real input's owner, not the unrelated installed authority.
    monkeypatch.setattr(
        modes, "_resolve_owner",
        lambda source, requested, **_kwargs: owner.resolve()
        if source == plugin and requested == owner
        else pytest.fail("Dev containment used the authority payload"),
    )
    assert modes.run(
        root, plugin, payload=authority, uv=tmp_path / "uv",
        base_python=Path(sys.executable), mode="dev", owner=owner,
    ) == {"runtime_version": "dev"}


def test_dev_owner_refuses_inputs_outside_claiming_worktree(modes, tmp_path):
    owner = tmp_path / "owner"
    owner.mkdir()
    subprocess.run(
        ["git", "-C", str(owner), "init", "-q"], check=True, **no_window_kwargs(),
    )
    plugin = owner / "plugin"
    plugin.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    assert modes._resolve_owner(plugin, owner, source_required=True) == owner.resolve()
    with pytest.raises(modes.lifecycle.InstallationRefused, match="claiming worktree"):
        modes._resolve_owner(outside, owner, source_required=True)


@pytest.mark.parametrize("construction_failure", [False, True])
@pytest.mark.parametrize("package_version", ["1.0.0-dev7", "1.0.0+local"])
def test_repair_guard_precedes_allocation_governance_and_real_construction(
    modes,
    tmp_path,
    monkeypatch,
    construction_failure,
    package_version,
):
    from agent_worktrees import status_monitor_cutover as cutover

    lifecycle = modes.lifecycle
    root, plugin = tmp_path / "runtime", tmp_path / "plugin"
    previous = root / "versions" / "broken"
    previous.mkdir(parents=True)
    (previous / ".install-complete.json").write_text("broken evidence", encoding="utf-8")
    (root / "current-version").write_text("broken\n", encoding="utf-8")
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text(
        f'[project]\nname="agent-worktrees"\nversion="{package_version}"\n',
        encoding="utf-8",
    )
    events = []
    guard = SimpleNamespace(release=lambda: events.append("release-cutover"))

    def acquire(*_args, **_kwargs):
        events.append("acquire-cutover")
        return guard

    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload

        def signature(self):
            return {"generation": 1}

        @contextmanager
        def held(self, *, timeout=180):
            assert events[0] == "acquire-cutover"
            events.append("enter-governance")
            try:
                yield
            finally:
                events.append("leave-governance")

    @contextmanager
    def candidate(requested, version, **kwargs):
        assert requested == root
        assert events == ["acquire-cutover", "enter-governance"]
        assert version.startswith(package_version)
        assert version != "broken" and "repair." in version
        assert kwargs["construction_data"] == provenance
        with pytest.raises(lifecycle.runtime.RetentionTimeout):
            with lifecycle.runtime._kernel_lease(
                root,
                "broken",
                kind="retention",
                shared=False,
                timeout=0,
            ):
                pytest.fail("broken predecessor lost retention during repair")
        events.append("construct")
        if construction_failure:
            raise OSError("construction storage failure")
        try:
            yield SimpleNamespace(payload_hash="frozen", reused=False)
        finally:
            events.append("release-candidate")

    def settle(requested, version, **kwargs):
        assert events == [
            "acquire-cutover",
            "enter-governance",
            "construct",
            "leave-governance",
        ]
        assert kwargs["cutover_guard"] is guard
        assert kwargs["lifecycle_slot"] == Path(sys.prefix).resolve()
        assert kwargs["package_version"] == package_version
        assert kwargs["previous"] == "broken"
        events.append("settle")
        return {"runtime_version": version, "package_version": package_version}

    monkeypatch.setattr(lifecycle, "Governance", Governance)
    monkeypatch.setattr(lifecycle, "_load_installed", lambda _slot: None)
    provenance = {
        "version": package_version, "source": "claiming-worktree",
        "commit": "frozen-commit", "branch": "frozen-branch",
        "deployment_source": '{"path":"claiming-worktree"}',
    }
    monkeypatch.setitem(
        sys.modules, "agent_worktrees._build_info", SimpleNamespace(BUILD_INFO=provenance),
    )
    monkeypatch.setattr(
        lifecycle, "_source_provenance",
        lambda *_args: pytest.fail("Mode owner must not reread live authority provenance"),
    )
    monkeypatch.setattr(cutover, "_acquire_cutover_lock", acquire)
    monkeypatch.setattr(lifecycle.engine, "numbered_candidate", candidate)
    monkeypatch.setattr(lifecycle, "settle_candidate", settle)
    arguments = dict(
        payload=plugin,
        uv=tmp_path / "uv",
        base_python=Path(sys.executable),
        mode="repair",
        expected_governance={"generation": 1},
    )
    if construction_failure:
        with pytest.raises(OSError, match="construction storage failure"):
            modes.run_owned(root, plugin, **arguments)
        assert events == [
            "acquire-cutover",
            "enter-governance",
            "construct",
            "leave-governance",
            "release-cutover",
        ]
    else:
        result = modes.run_owned(root, plugin, **arguments)
        assert result["package_version"] == package_version
        assert result["runtime_version"] != package_version
        assert events[-3:] == ["settle", "release-candidate", "release-cutover"]
    assert (previous / ".install-complete.json").read_text() == "broken evidence"
    with lifecycle.runtime._kernel_lease(
        root, "broken", kind="retention", shared=False, timeout=0
    ):
        pass


def test_repair_refuses_changed_governance_before_acquiring_cutover(modes, tmp_path, monkeypatch):
    from agent_worktrees import status_monitor_cutover as cutover

    monkeypatch.setattr(modes.lifecycle, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(
        modes.lifecycle,
        "Governance",
        lambda *_args: SimpleNamespace(
            root=tmp_path,
            payload=tmp_path,
            signature=lambda: {"generation": 2},
        ),
    )
    monkeypatch.setattr(
        cutover,
        "_acquire_cutover_lock",
        lambda *_args, **_kwargs: pytest.fail(
            "changed installation authority acquired cutover",
        ),
    )
    with pytest.raises(modes.lifecycle.InstallationRefused, match="governance changed"):
        modes.run_owned(
            tmp_path,
            tmp_path,
            payload=tmp_path,
            uv=tmp_path / "uv",
            base_python=Path(sys.executable),
            mode="repair",
            expected_governance={"generation": 1},
        )


def test_dev_candidate_refreshes_only_claimed_slot_and_retains_it(modes, tmp_path, monkeypatch):
    lifecycle = modes.lifecycle
    root, plugin = tmp_path / "runtime", tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "scripts").mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py", "versioned_runtime_publication.py",
    ):
        shutil.copyfile(lifecycle.SCRIPTS / name, plugin / "scripts" / name)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="1.0.0"\n',
        encoding="utf-8",
    )
    (plugin / "source.py").write_text("first", encoding="utf-8")
    owner = str(tmp_path / "owner")
    lifecycle.runtime.claim_dev(root, owner, previous_version="numbered")
    constructions = []

    def construct(inputs, slot, *_args, refresh=False):
        assert slot == root / "versions" / "dev" and refresh
        assert not lifecycle.runtime.marker_path(root, "dev").exists()
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"interpreter")
        (slot / "pyvenv.cfg").write_text("test", encoding="utf-8")
        source = (inputs.plugin / "source.py").read_text()
        (slot / "observed-source").write_text(source, encoding="utf-8")
        constructions.append(source)
        return python

    monkeypatch.setattr(lifecycle.engine, "_construct", construct)
    from contextlib import nullcontext
    monkeypatch.setattr(
        lifecycle.engine, "_retained_inputs", lambda inputs, *_args, **_kwargs: nullcontext(inputs),
    )
    monkeypatch.setattr(lifecycle.engine, "_prepare_installed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle.engine, "_verify_installed", lambda *_args, **_kwargs: None)
    arguments = dict(
        plugin=plugin,
        uv=tmp_path / "uv",
        base_python=Path(sys.executable),
        health_module="agent_worktrees.runtime_candidate",
        process_retention=True,
    )
    hashes = []
    for source in ("first", "second"):
        (plugin / "source.py").write_text(source, encoding="utf-8")
        with lifecycle.engine.dev_candidate(root, owner=owner, **arguments) as candidate:
            hashes.append(candidate.payload_hash)
            assert candidate.reused is False
            assert (candidate.slot / "observed-source").read_text() == source
            with pytest.raises(lifecycle.runtime.RetentionTimeout):
                with lifecycle.runtime._kernel_lease(
                    root,
                    "dev",
                    kind="retention",
                    shared=False,
                    timeout=0,
                ):
                    pytest.fail("dev candidate was not retained")
            with lifecycle.runtime.construction_lease(root, "dev", timeout=0):
                pass
    assert constructions == ["first", "second"] and hashes[0] != hashes[1]
    assert lifecycle.runtime.read_dev_claim(root)["previous_version"] == "numbered"
    marker = lifecycle.runtime.marker_path(root, "dev").read_bytes()
    with pytest.raises(RuntimeError, match="explicit dev claim"):
        with lifecycle.engine.dev_candidate(root, owner="other", **arguments):
            pytest.fail("non-owner constructed dev")
    assert lifecycle.runtime.marker_path(root, "dev").read_bytes() == marker


@pytest.mark.parametrize("settlement_failure", [False, True])
def test_dev_release_keeps_original_claim_until_restoration_settles(
    modes,
    tmp_path,
    monkeypatch,
    settlement_failure,
):
    from agent_worktrees import status_monitor_cutover as cutover

    lifecycle = modes.lifecycle
    root, plugin = tmp_path / "runtime", tmp_path / "plugin"
    slot = root / "versions" / "original"
    slot.mkdir(parents=True)
    lifecycle.runtime.mark_complete(root, "original", payload_hash="original-content")
    (root / "versions" / "dev").mkdir()
    owner = tmp_path / "owner"
    lifecycle.runtime.claim_dev(root, str(owner), previous_version="original")
    lifecycle.runtime.mark_complete(root, "dev", payload_hash="dev-content")
    lifecycle.runtime.activate(root, "dev", link_free=True)
    events = []

    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload

        def signature(self):
            return {}

        @contextmanager
        def held(self, *, timeout=180):
            assert "enter-governance" not in events[-1:]
            events.append("enter-governance")
            try:
                yield
            finally:
                events.append("leave-governance")

    guard = SimpleNamespace(release=lambda: events.append("release-cutover"))

    def settle(requested, version, **kwargs):
        assert version == "original" and kwargs["previous"] == "dev"
        assert kwargs["cutover_guard"] is guard
        assert kwargs["payload_hash"] == "original-content"
        assert kwargs["package_version"] == "1.0.0"
        assert events == ["enter-governance", "leave-governance"]
        assert lifecycle.runtime.read_dev_claim(root)["previous_version"] == "original"
        for retained in ("original", "dev"):
            with pytest.raises(lifecycle.runtime.RetentionTimeout):
                with lifecycle.runtime._kernel_lease(
                    root,
                    retained,
                    kind="retention",
                    shared=False,
                    timeout=0,
                ):
                    pytest.fail("release lost generation retention")
        if settlement_failure:
            raise RuntimeError("unsettled cutover")
        lifecycle.runtime.activate(root, version, link_free=True)
        return {"runtime_version": version}

    monkeypatch.setattr(lifecycle, "Governance", Governance)
    monkeypatch.setattr(lifecycle, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(cutover, "_acquire_cutover_lock", lambda *_args, **_kwargs: guard)
    monkeypatch.setattr(
        lifecycle.engine, "freeze_inputs",
        lambda *_args: pytest.fail("Release must not read today's source graph"),
    )
    monkeypatch.setattr(
        lifecycle.engine, "run_owned", lambda *_args, **_kwargs: SimpleNamespace(stdout='"1.0.0"')
    )
    monkeypatch.setattr(lifecycle, "settle_candidate", settle)
    (root / ".installer-inputs").mkdir()
    arguments = dict(
        payload=plugin,
        mode="dev-release",
        owner=owner,
        expected_governance={},
    )
    if settlement_failure:
        with pytest.raises(RuntimeError, match="unsettled cutover"):
            modes.run_owned(root, plugin, **arguments)
        assert lifecycle.runtime.read_dev_claim(root)["owner"] == str(owner)
        assert lifecycle.runtime.current_version(root) == "dev"
    else:
        assert modes.run_owned(root, plugin, **arguments)["reused"] is True
        assert lifecycle.runtime.read_dev_claim(root) is None
        assert lifecycle.runtime.current_version(root) == "original"
    assert events[-1] == "release-cutover"


@pytest.mark.timeout(650)
@pytest.mark.parametrize("entrypoint", ["powershell.exe", "pwsh.exe", "bash"])
def test_real_native_repair_preserves_broken_slot_and_allocates_distinct_generations(
    tmp_path, entrypoint, modes
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("real repair requires the configured uv executable")
    plugin = Path(__file__).resolve().parents[1]
    engine = modes.lifecycle.engine
    original_hash = modes.lifecycle.runtime.fingerprint_source(
        engine._source_files(engine._package_roots(plugin)),
    )
    version = tomllib.loads((plugin / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    root, home = tmp_path / "runtime", tmp_path / "home"
    broken = root / "versions" / version
    broken.mkdir(parents=True)
    home.mkdir()
    (broken / ".install-complete.json").write_text("broken completion evidence", encoding="utf-8")
    (broken / "do-not-rewrite").write_bytes(b"original published runtime")
    (root / "current-version").write_text(version + "\n", encoding="utf-8")
    (root / "last-known-good").write_text(version + "\n", encoding="utf-8")
    environment = dict(os.environ)
    environment.update(HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT",
        "COPILOT_PLUGIN_ROOT",
        "COPILOT_PLUGIN_STAGED_FROM",
        "AGENT_WORKTREES_PAYLOAD_ROOT",
    ):
        environment.pop(name, None)
    commands = []
    if os.name == "nt":
        for shell in ("powershell.exe", "pwsh.exe"):
            if shell != entrypoint:
                continue
            if executable := shutil.which(shell):
                commands.append(
                    [
                        executable,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(plugin / "scripts" / "install.ps1"),
                        "repair",
                        "-InstallDir",
                        str(root),
                    ]
                )
        git = shutil.which("git")
        bash = Path(git).resolve().parent.parent / "bin" / "bash.exe" if git else None
        if entrypoint == "bash" and bash is not None and bash.is_file():
            commands.append(
                [
                    str(bash),
                    "-c",
                    'exec bash "$1" repair --install-dir "$(cygpath -u "$2")"',
                    "--",
                    (plugin / "scripts" / "install.sh").as_posix(),
                    str(root),
                ]
            )
    elif entrypoint == "bash" and (bash := shutil.which("bash")):
        commands.append(
            [
                bash,
                str(plugin / "scripts" / "install.sh"),
                "repair",
                "--install-dir",
                str(root),
            ]
        )
    if not commands:
        pytest.skip(f"native entrypoint unavailable: {entrypoint}")
    completed = {}
    for index, argv in enumerate(commands):
        log = tmp_path / f"repair-{index}.log"
        with log.open("w", encoding="utf-8") as stream:
            try:
                result = subprocess.run(
                    argv,
                    env=environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=240,
                    **no_window_kwargs(),
                )
            except subprocess.TimeoutExpired:
                pytest.fail("Native repair did not settle:\n" + log.read_text(encoding="utf-8"))
        assert result.returncode == 0, log.read_text(encoding="utf-8")
        selected = (root / "current-version").read_text().strip()
        assert selected.startswith(version + "+repair.") and selected not in completed
        manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
        assert manifest["source"]["version"] == version
        assert manifest["runtime_slot"] == selected
        assert len(manifest["runtime_generation"]) == 32
        assert Path(manifest["venv"]).resolve() == (root / "versions" / selected).resolve()
        for prior, marker in completed.items():
            assert (root / "versions" / prior / ".install-complete.json").read_bytes() == marker
        completed[selected] = (
            root / "versions" / selected / ".install-complete.json"
        ).read_bytes()
        current_hash = modes.lifecycle.runtime.fingerprint_source(
            engine._source_files(engine._package_roots(plugin)),
        )
        assert current_hash == original_hash, "The admitted original source changed during native repair"
        assert json.loads(completed[selected])["payload_hash"] == original_hash, (
            "Repair completion describes another input graph instead of the admitted original source"
        )
        assert (broken / ".install-complete.json").read_text() == "broken completion evidence"
        assert (broken / "do-not-rewrite").read_bytes() == b"original published runtime"
        reuse_command = [("provision" if argument == "repair" else argument) for argument in argv]
        if entrypoint == "bash" and os.name == "nt":
            reuse_command[2] = reuse_command[2].replace(" repair ", " provision ")
        reuse_log = tmp_path / f"reuse-{index}.log"
        with reuse_log.open("w", encoding="utf-8") as stream:
            reuse = subprocess.run(
                reuse_command, env=environment, stdout=stream,
                stderr=subprocess.STDOUT, timeout=240, **no_window_kwargs(),
            )
        assert reuse.returncode == 0, reuse_log.read_text(encoding="utf-8")
        assert (root / "current-version").read_text().strip() == selected
        reused_manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
        assert reused_manifest["source"] == manifest["source"]
        assert reused_manifest["runtime_generation"] == manifest["runtime_generation"]
        assert (root / "versions" / selected / ".install-complete.json").read_bytes() == (
            completed[selected]
        )
        assert (broken / ".install-complete.json").read_text() == "broken completion evidence"
    assert not list((root / ".installer-inputs").glob("mode-owner-*"))
