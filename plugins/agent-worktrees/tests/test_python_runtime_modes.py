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
from runtime_install_fixture import run_native_installer


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


@pytest.mark.parametrize(("mode", "recover_manager"), [
    ("dev", False), ("repair", False), ("repair", True),
])
@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("context", ["", "namespaced"])
def test_mode_bootstrap_attributes_inputs_and_retains_recovery_intent(
    modes, tmp_path, monkeypatch, mode, recover_manager, retained, context,
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
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", "previous-payload")
    provenance = {"version": "1.0.0", "source": str(plugin),
                  "deployment_source": json.dumps({"path": plugin.as_posix()})}
    expected_hash = modes.lifecycle.runtime.fingerprint_source(
        modes.lifecycle.engine._source_files((plugin,)),
    )

    @contextmanager
    def held(**_kwargs):
        yield

    monkeypatch.setattr(
        modes.lifecycle, "Governance",
        lambda requested, payload: SimpleNamespace(
            root=requested, payload=payload, context=context,
            held=held, signature=lambda: {"generation": 1},
        ),
    )

    def source_provenance(source, version, identity):
        assert source == plugin and version == "1.0.0"
        assert identity is None
        return provenance

    @contextmanager
    def candidate(control_root, version, **kwargs):
        assert not retained, "compatible installed control must avoid a second construction"
        assert version == "control" and kwargs["plugin"].is_relative_to(control_root)
        assert (kwargs["plugin"] / "pyproject.toml").read_bytes() == (plugin / "pyproject.toml").read_bytes()
        assert kwargs["construction_data"] == provenance
        assert kwargs["expected_payload_hash"] == expected_hash
        assert kwargs["source_wheel_cache_root"] == root
        assert os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"] == str(authority)
        assert kwargs["construction_data"]["source"] == str(plugin)
        assert json.loads(kwargs["construction_data"]["deployment_source"])["path"] == authority.as_posix()
        yield SimpleNamespace(frozen_plugin=control_root / "frozen")

    class OwnerProcess:
        returncode = 0

        def __init__(self, argv, **_kwargs):
            assert argv[argv.index("--payload") + 1] == str(authority)
            if mode == "dev":
                assert argv[argv.index("--owner") + 1] == str(owner.resolve())
            else:
                assert "--owner" not in argv
            assert ("--recover-adopted-manager" in argv) is recover_manager
            assert argv[argv.index("--payload-hash") + 1] == expected_hash
            if retained:
                assert json.loads(argv[argv.index("--construction-provenance") + 1]) == provenance
            Path(argv[argv.index("--result") + 1]).write_text(json.dumps({"runtime_version": mode}))

        def wait(self):
            return self.returncode

    monkeypatch.setattr(modes.lifecycle, "_source_provenance", source_provenance)
    monkeypatch.setattr(modes.lifecycle.engine, "numbered_candidate", candidate)
    monkeypatch.setattr(
        modes.lifecycle.runtime, "slot_python", lambda *_args: Path(sys.executable),
    )
    monkeypatch.setattr(modes.subprocess, "Popen", OwnerProcess)
    monkeypatch.setattr(
        modes, "_retained_control",
        lambda *_args, **_kwargs: (Path(sys.executable), 17, "1.0.0") if retained else None,
    )
    if retained:
        monkeypatch.setattr(modes, "os", SimpleNamespace(name="nt", environ=os.environ))
        monkeypatch.setattr(modes.lifecycle.engine, "_procutil", lambda _inputs: None)

        def run_owned(argv, **options):
            assert options["runtime_root"] == root
            assert options["runtime_version"] == "1.0.0"
            assert options["construction_fd"] == 17 and options["timeout"] == 480
            assert options["allow_breakaway"] is True
            assert Path(options["retention_scope"]).name == "retention-scope"
            OwnerProcess(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(modes.lifecycle.engine, "run_owned", run_owned)
    # Git resolves the real input's owner, not the unrelated installed authority.
    monkeypatch.setattr(
        modes, "_resolve_owner",
        lambda source, requested, **_kwargs: owner.resolve()
        if source == plugin and requested == owner
        else pytest.fail("Dev containment used the authority payload"),
    )
    assert modes.run(
        root, plugin, payload=authority, uv=tmp_path / "uv",
        base_python=Path(sys.executable), mode=mode, owner=owner if mode == "dev" else None,
        recover_adopted_manager=recover_manager,
    ) == {"runtime_version": mode}
    assert os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"] == "previous-payload"


@pytest.mark.parametrize("compatible", [False, True])
def test_installed_control_keeps_canonical_retention_through_dispatch(
    modes, tmp_path, monkeypatch, compatible,
):
    runtime = modes.lifecycle.runtime
    version = "1.0.0"
    slot = runtime.slot(tmp_path, version)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    runtime.mark_complete(tmp_path, version, payload_hash="a" * 64)
    (tmp_path / "current-version").write_text(version + "\n", encoding="utf-8")
    observed = []
    monkeypatch.setattr(modes.lifecycle.engine, "_procutil", lambda _inputs: None)

    def probe(argv, **options):
        descriptor = options["construction_fd"]
        os.fstat(descriptor)
        observed.append(descriptor)
        assert options["runtime_root"] == tmp_path and options["runtime_version"] == version
        assert Path(argv[0]) == python and str(modes._MODE_OWNER_ABI) == argv[-1]
        with pytest.raises(runtime.RetentionTimeout):
            with runtime._kernel_lease(
                tmp_path, version, kind="retention", shared=False, timeout=0,
            ):
                pytest.fail("installed control lost its retention during health")
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"version": 1, "compatible": compatible}), "",
        )

    monkeypatch.setattr(modes.lifecycle.engine, "run_owned", probe)
    from contextlib import ExitStack

    with ExitStack() as custody:
        result = modes._retained_control(
            tmp_path, None, custody, mode="dev", wait_timeout=1, child_timeout=3,
        )
        if compatible:
            assert result == (python, observed[0], version)
            os.fstat(observed[0])
            with pytest.raises(runtime.RetentionTimeout):
                with runtime._kernel_lease(
                    tmp_path, version, kind="retention", shared=False, timeout=0,
                ):
                    pytest.fail("installed control lost retention before dispatch settled")
        else:
            assert result is None
    assert len(observed) == 1
    with pytest.raises(OSError):
        os.fstat(observed[0])


def test_repair_does_not_execute_the_generation_being_repaired(modes, tmp_path, monkeypatch):
    (tmp_path / "current-version").write_text("1.0.0\n", encoding="utf-8")
    (tmp_path / "last-known-good").write_text("1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(
        modes.lifecycle.engine, "run_owned",
        lambda *_args, **_kwargs: pytest.fail("repair cannot execute its damaged target as control"),
    )
    from contextlib import ExitStack

    with ExitStack() as custody:
        assert modes._retained_control(
            tmp_path, None, custody, mode="repair", wait_timeout=1, child_timeout=3,
        ) is None


@pytest.mark.parametrize("replacement_present", [False, True])
def test_installed_control_requires_monitor_replacement_capability(
    modes, tmp_path, monkeypatch, replacement_present,
):
    from contextlib import ExitStack

    runtime = modes.lifecycle.runtime
    version = "1.0.0"
    slot = runtime.slot(tmp_path, version)
    package = slot / "agent_worktrees"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    health = slot / "health-checked"
    (package / "runtime_candidate.py").write_text(
        f"MODE_OWNER_ABI = {modes._MODE_OWNER_ABI!r}\n"
        f"def verify_runtime_candidate(): open({str(health)!r},'w').close()\n"
        "def verify_installed_origins(): pass\n",
    )
    if replacement_present:
        (package / "runtime_monitor_replacement.py").write_text("")
    (slot / ".runtime-input-origins.json").write_text("{}")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    runtime.mark_complete(tmp_path, version, payload_hash="a" * 64)
    (tmp_path / "current-version").write_text(version + "\n")
    monkeypatch.setattr(modes.lifecycle.engine, "_procutil", lambda _inputs: None)

    def run_probe(argv, **_kwargs):
        source = (
            f"import sys; sys.prefix={str(slot)!r}; sys.path.insert(0,{str(slot)!r})\n"
            + argv[4]
        )
        return subprocess.run(
            [sys.executable, "-I", "-B", "-c", source, *argv[5:]],
            check=True, capture_output=True, text=True, **no_window_kwargs(),
        )

    monkeypatch.setattr(modes.lifecycle.engine, "run_owned", run_probe)
    with ExitStack() as custody:
        control = modes._retained_control(
            tmp_path, None, custody, mode="dev", wait_timeout=1, child_timeout=5,
        )
        assert (control is not None) is replacement_present
    assert health.exists() is replacement_present


@pytest.mark.parametrize("provenance", [{}, {"version": "1.0.0"}, None])
def test_borrowed_control_provenance_refuses_without_an_admitted_source_pin(
    modes, tmp_path, monkeypatch, provenance,
):
    if provenance is None:
        provenance = dict.fromkeys(
            ("version", "source", "commit", "branch", "deployment_source"), "fixture",
        )
    monkeypatch.setattr(
        modes.lifecycle, "Governance",
        lambda *_args, **_kwargs: pytest.fail("invalid construction evidence reached mutation authority"),
    )
    with pytest.raises(modes.lifecycle.InstallationRefused, match="frozen construction provenance"):
        modes.run_owned(
            tmp_path, tmp_path, payload=tmp_path, uv=tmp_path / "uv",
            base_python=Path(sys.executable), mode="dev",
            expected_governance={}, construction_provenance=provenance,
        )


@pytest.mark.parametrize("pin", [None, "a" * 64])
def test_mode_owner_cli_forwards_optional_frozen_payload_pin(modes, tmp_path, monkeypatch, pin):
    root, plugin = tmp_path / "runtime", tmp_path / "plugin"

    def run_owned(requested_root, requested_plugin, **kwargs):
        assert requested_root == root and requested_plugin == plugin
        assert kwargs["expected_governance"] == {"generation": 1}
        assert ("expected_payload_hash" in kwargs) is (pin is not None)
        if pin is not None:
            assert kwargs["expected_payload_hash"] == pin
        return {"runtime_version": "dev-" + "a" * 64}

    monkeypatch.setattr(modes, "run_owned", run_owned)
    arguments = [
        "--root", str(root), "--plugin", str(plugin), "--payload", str(plugin),
        "--uv", str(tmp_path / "uv"), "--base-python", sys.executable,
        "--result", str(tmp_path / "result.json"), "--mode", "dev", "--mode-owner",
        "--governance-generation", '{"generation":1}',
    ]
    if pin is not None:
        arguments.extend(["--payload-hash", pin])
    assert modes.lifecycle.main(arguments) == 0


@pytest.mark.parametrize("pin", ["wrong", "a" * 63, "g" * 64])
def test_helper_publication_refuses_malformed_source_hash(modes, tmp_path, pin):
    with pytest.raises(modes.lifecycle.InstallationRefused, match="full source SHA256"):
        modes.lifecycle._publish_helpers(
            None, {"shell": "ps1"}, tmp_path, tmp_path, 0,
            timeout=10, base_python=Path(sys.executable), expected_payload_hash=pin,
        )


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


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("recover_manager", [False, True])
def test_mode_cli_retains_requested_manager_recovery(
    modes, tmp_path, monkeypatch, owned, recover_manager,
):
    calls = []

    def run(root, plugin, **kwargs):
        assert kwargs["recover_adopted_manager"] is recover_manager
        calls.append(owned)
        return {"runtime_version": "distinct-repair"}

    monkeypatch.setattr(modes, "run_owned" if owned else "run", run)
    arguments = [
        "--root", str(tmp_path / "runtime"), "--plugin", str(tmp_path / "plugin"),
        "--payload", str(tmp_path / "owner"), "--uv", str(tmp_path / "uv"),
        "--base-python", sys.executable, "--result", str(tmp_path / "result.json"),
        "--mode", "repair",
    ]
    if owned:
        arguments += ["--mode-owner", "--governance-generation", "{}"]
    if recover_manager:
        arguments.append("--recover-adopted-manager")
    assert modes.lifecycle.main(arguments) == 0
    assert calls == [owned]
@pytest.mark.parametrize("length", [32, 64])
def test_dev_generation_requires_claim_owner_for_completion_and_selection(modes, tmp_path, length):
    runtime = modes.lifecycle.runtime
    version = "dev-" + "a" * length
    runtime.claim_dev(tmp_path, "owner", previous_version="original")
    runtime.slot(tmp_path, version)
    for operation in (
        lambda owner: runtime.mark_complete(
            tmp_path, version, payload_hash="content", dev_owner=owner,
        ),
        lambda owner: runtime.activate(tmp_path, version, dev_owner=owner, link_free=True),
    ):
        with pytest.raises(RuntimeError, match="claim owner"):
            operation("other")
        operation("owner")
    assert runtime.current_version(tmp_path) == version
    claim = runtime.read_dev_claim(tmp_path, strict=True)
    assert claim["selected_generation"] == version and claim["previous_version"] == "original"
    runtime.gc(tmp_path, keep=[], min_age_days=0, protect_pids=False)
    assert runtime.is_complete(tmp_path, version, expect_hash="content")


@pytest.mark.parametrize("construction_failure", [False, True])
@pytest.mark.parametrize("package_version", ["1.0.0-dev7", "1.0.0+local"])
@pytest.mark.parametrize("recover_manager", [False, True])
@pytest.mark.parametrize("mode", ["repair", "dev"])
@pytest.mark.parametrize("retained_control", [False, True])
def test_mode_namespace_then_cutover_precede_allocation_and_construction(
    modes,
    tmp_path,
    monkeypatch,
    construction_failure,
    package_version,
    recover_manager,
    mode,
    retained_control,
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
    owner = tmp_path / "owner"
    generation = "dev-" + "a" * 64
    guard = SimpleNamespace(release=lambda: events.append("release-cutover"))

    def acquire(*_args, **_kwargs):
        events.append("acquire-cutover")
        return guard

    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload
            self.active = False

        def signature(self):
            return {"generation": 1}

        @contextmanager
        def held(self, *, timeout=180):
            if self.active:
                yield
                return
            self.active = True
            events.append("enter-governance")
            try:
                yield
            finally:
                events.append("leave-governance")
                self.active = False

    @contextmanager
    def candidate(requested, version=None, **kwargs):
        assert requested == root
        assert events == ["enter-governance", "acquire-cutover"]
        if mode == "dev":
            assert version is None and kwargs["owner"] == str(owner)
            assert lifecycle.runtime.read_dev_claim(root, strict=True)["previous_version"] == "broken"
            version = generation
        else:
            assert version.startswith(package_version)
            assert version != "broken" and "repair." in version
        assert kwargs["construction_data"] == provenance
        assert kwargs["expected_payload_hash"] == "a" * 64
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
            yield SimpleNamespace(
                payload_hash="frozen", reused=False, slot=root / "versions" / version,
            )
        finally:
            events.append("release-candidate")

    def settle(requested, version, **kwargs):
        assert events == [
            "enter-governance",
            "acquire-cutover",
            "construct",
        ]
        assert kwargs["cutover_guard"] is guard
        assert kwargs["governance"].active
        assert kwargs["lifecycle_slot"] == Path(sys.prefix).resolve()
        assert kwargs["package_version"] == package_version
        assert kwargs["previous"] == "broken"
        assert kwargs["recover_adopted_manager"] is recover_manager
        assert kwargs["dev_owner"] == (str(owner) if mode == "dev" else None)
        assert kwargs["publication_payload_hash"] == "a" * 64
        assert not kwargs.get("force_monitor_transition", False)
        if mode == "dev":
            assert version == generation
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
        sys.modules, "agent_worktrees._build_info",
        SimpleNamespace(BUILD_INFO={**provenance, "version": "0.0.1"} if retained_control else provenance),
    )
    monkeypatch.setattr(
        lifecycle, "_source_provenance",
        lambda *_args: pytest.fail("Mode owner must not reread live authority provenance"),
    )
    monkeypatch.setattr(cutover, "_acquire_cutover_lock", acquire)
    monkeypatch.setattr(lifecycle.engine, "numbered_candidate", candidate)
    monkeypatch.setattr(lifecycle.engine, "dev_candidate", candidate)
    monkeypatch.setattr(lifecycle, "settle_candidate", settle)
    arguments = dict(
        payload=plugin,
        uv=tmp_path / "uv",
        base_python=Path(sys.executable),
        mode=mode,
        owner=owner,
        expected_payload_hash="a" * 64,
        expected_governance={"generation": 1},
        recover_adopted_manager=recover_manager,
    )
    if retained_control:
        arguments["construction_provenance"] = provenance
    if construction_failure:
        with pytest.raises(OSError, match="construction storage failure"):
            modes.run_owned(root, plugin, **arguments)
        assert events == [
            "enter-governance",
            "acquire-cutover",
            "construct",
            "release-cutover",
            "leave-governance",
        ]
    else:
        result = modes.run_owned(root, plugin, **arguments)
        assert result["package_version"] == package_version
        assert result["runtime_version"] != package_version
        assert events[-4:] == [
            "settle", "release-candidate", "release-cutover", "leave-governance",
        ]
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


@pytest.mark.parametrize("blocked_by", ["pending", "serving"])
@pytest.mark.parametrize("selected", ["dev", "current"])
def test_dev_rebuild_refuses_live_code_before_claim_or_construction(
    modes, tmp_path, monkeypatch, blocked_by, selected,
):
    from contextlib import nullcontext
    from agent_worktrees import runtime_monitor_retirements as retirements
    from agent_worktrees import status_monitor_cutover as cutover

    lifecycle = modes.lifecycle
    root, plugin = tmp_path / "runtime", tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="1.0.0"\n', encoding="utf-8",
    )
    claim = lifecycle.runtime.claim_dev(root, "original-owner", previous_version="numbered")
    (root / "versions" / "dev").mkdir(parents=True, exist_ok=True)
    lifecycle.runtime.mark_complete(root, "dev", payload_hash="dev-content")
    if selected == "current":
        (root / "versions" / selected).mkdir()
        lifecycle.runtime.mark_complete(root, selected, payload_hash="numbered-content")
    lifecycle.runtime.activate(root, selected, link_free=True)
    released = []
    monkeypatch.setattr(lifecycle, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(
        lifecycle, "Governance",
        lambda *_args: SimpleNamespace(
            root=root, payload=plugin, signature=lambda: {},
            held=lambda **_kwargs: nullcontext(),
        ),
    )
    monkeypatch.setattr(
        cutover, "_acquire_cutover_lock",
        lambda *_args, **_kwargs: SimpleNamespace(release=lambda: released.append(True)),
    )
    monkeypatch.setattr(
        retirements, "pending_slot",
        lambda requested, slot: blocked_by == "pending" and requested == root and slot == "dev",
    )
    monkeypatch.setattr(lifecycle, "_monitor_identity", lambda requested: (101, "1001"))
    monkeypatch.setattr(
        retirements.locks, "read_lock",
        lambda *_args: {
            "pid": 101, "start_time": "1001", "prefix": str(root / "versions" / "dev"),
        },
    )
    monkeypatch.setattr(retirements.locks, "lock_is_live", lambda _record: True)
    monkeypatch.setattr(retirements.locks, "process_start_time", lambda _pid: "1001")
    monkeypatch.setattr(
        lifecycle.runtime, "claim_dev",
        lambda *_args, **_kwargs: pytest.fail("Live dev code was claimed before retirement"),
    )
    monkeypatch.setattr(
        lifecycle.engine, "dev_candidate",
        lambda *_args, **_kwargs: pytest.fail("Retiring dev runtime was reconstructed"),
    )
    message = "previous dev resident is still retiring" if blocked_by == "pending" else "must retire before reconstruction"
    with pytest.raises(lifecycle.InstallationRefused, match=message):
        modes.run_owned(
            root, plugin, payload=plugin, uv=tmp_path / "uv",
            base_python=Path(sys.executable), mode="dev", owner=tmp_path / "new-owner",
            expected_governance={},
        )
    assert lifecycle.runtime.read_dev_claim(root) == claim
    assert released == [True]


@pytest.mark.parametrize("blocked_by", ["claim", "slot"])
def test_duplicate_dev_release_refuses_while_original_claim_is_retiring(
    modes, tmp_path, monkeypatch, blocked_by,
):
    from contextlib import ExitStack, nullcontext
    from agent_worktrees import runtime_monitor_retirements as retirements
    from single_instance_lease import SingleInstance

    root = tmp_path / "runtime"
    owner = tmp_path / "owner"
    claim = modes.lifecycle.runtime.claim_dev(root, str(owner), previous_version="original")
    monkeypatch.setattr(
        retirements, "pending_claim",
        lambda requested, original: blocked_by == "claim" and requested == root and original == claim,
    )
    def pending_slot(requested, slot, *, include_cancelled=True):
        assert requested == root and slot == "dev" and not include_cancelled
        return blocked_by == "slot"

    monkeypatch.setattr(retirements, "pending_slot", pending_slot)
    governance = SimpleNamespace(held=lambda **kwargs: nullcontext())
    with ExitStack() as custody:
        guard = custody.enter_context(
            SingleInstance(root / "status-monitor-routing", lock_name="cutover.lock"),
        )
        with pytest.raises(modes.lifecycle.InstallationRefused, match="pending resident retirement"):
            modes._release(
                root, tmp_path, governance, custody, guard, tmp_path,
                owner=owner, wait_timeout=0.1, child_timeout=0.1,
            )
    assert modes.lifecycle.runtime.read_dev_claim(root) == claim


@pytest.fixture
def retired_mode_runtime(modes, tmp_path, monkeypatch):
    from contextlib import nullcontext

    from zdd import process_exit

    from agent_worktrees import runtime_monitor_retirements as retirements
    from agent_worktrees import status_monitor_cutover as cutover

    helper = (
        Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    )
    spec = importlib.util.spec_from_file_location("mode_retirement_runtime", helper)
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(modes.lifecycle, "runtime", runtime)
    monkeypatch.setattr(retirements, "_runtime", lambda: runtime)
    root, plugin, owner = tmp_path / "runtime", tmp_path / "plugin", tmp_path / "owner"
    for version in ("original", "dev"):
        (root / "versions" / version).mkdir(parents=True)
    runtime.mark_complete(root, "original", payload_hash="original")
    runtime.activate(root, "original", link_free=True)
    claim = runtime.claim_dev(root, str(owner), previous_version="original")
    runtime.mark_complete(root, "dev", payload_hash="dev")
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="1.0.0"\n', encoding="utf-8",
    )
    governance = SimpleNamespace(
        root=root, payload=plugin, signature=lambda: {},
        held=lambda **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(modes.lifecycle, "Governance", lambda *_args: governance)
    monkeypatch.setattr(modes.lifecycle, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(modes.lifecycle, "_monitor_identity", lambda _root: None)
    monkeypatch.setitem(sys.modules, "agent_worktrees._build_info", SimpleNamespace(
        BUILD_INFO=dict(version="1.0.0", source=str(plugin), commit=None, branch=None,
                        deployment_source="{}"),
    ))
    observation = {"exited": False}

    def native_exit(pid, start):
        assert (pid, start) == (101, "1001")
        if observation["exited"] is None:
            raise PermissionError("native identity unavailable")
        return observation["exited"]

    monkeypatch.setattr(process_exit, "observe_original_exit", native_exit)
    predecessor = dict(pid=101, start="1001", slot="dev", release_claim=claim)

    def reserve(state, exited):
        # Publish references through the actual SDK while the original is live.
        retirements.reserve(root, predecessor, runtime=runtime, committed=state == "retiring")
        if state == "cancelled":
            retirements.cancel(root, predecessor, runtime=runtime)
        observation["exited"] = exited
        assert retirements.pending_slot(root, "dev")

    def release():
        from contextlib import ExitStack

        with ExitStack() as custody:
            guard = cutover._acquire_cutover_lock(root / "status-monitor-routing", timeout_s=0.1)
            custody.callback(guard.release)
            return modes._release(
                root, plugin, governance, custody, guard, tmp_path,
                owner=owner, wait_timeout=0.1, child_timeout=0.1,
            )

    return SimpleNamespace(
        root=root, plugin=plugin, owner=owner, runtime=runtime, claim=claim,
        reserve=reserve, release=release, retirements=retirements,
    )


@pytest.mark.parametrize(("state", "exited"), [
    ("cancelled", True), ("retiring", True), ("prepared", True),
    ("cancelled", False), ("retiring", False),
    ("cancelled", None), ("retiring", None),
])
def test_dev_rebuild_reconciles_only_settled_native_retirements(
    modes, retired_mode_runtime, tmp_path, monkeypatch, state, exited,
):
    fixture = retired_mode_runtime
    fixture.reserve(state, exited)
    constructed = []
    generation = "dev-" + "a" * 64

    @contextmanager
    def candidate(root, *, owner, **_kwargs):
        assert root == fixture.root and owner == str(fixture.owner)
        assert not fixture.retirements.pending_slot(root, "dev")
        assert not fixture.runtime._leases.read_runtime_references(root)
        assert fixture.runtime.read_dev_claim(root)["previous_version"] == "original"
        constructed.append(True)
        yield SimpleNamespace(
            slot=root / "versions" / generation, payload_hash="new-dev", reused=False,
        )

    def settle(root, version, **kwargs):
        assert version == generation and kwargs["dev_owner"] == str(fixture.owner)
        assert kwargs["previous"] == "original"
        assert kwargs["cutover_guard"].held
        return {"runtime_version": version}

    monkeypatch.setattr(modes.lifecycle.engine, "dev_candidate", candidate)
    monkeypatch.setattr(modes.lifecycle, "settle_candidate", settle)
    arguments = dict(
        payload=fixture.plugin, uv=tmp_path / "uv", base_python=Path(sys.executable),
        mode="dev", owner=fixture.owner, expected_governance={}, wait_timeout=0.1,
    )
    if exited is True and state != "prepared":
        result = modes.run_owned(fixture.root, fixture.plugin, **arguments)
        assert result["runtime_version"] == generation
        assert constructed == [True]
    else:
        message = (
            "native identity" if exited is None else "previous dev resident is still retiring"
        )
        with pytest.raises(RuntimeError, match=message):
            modes.run_owned(fixture.root, fixture.plugin, **arguments)
        assert not constructed
        assert fixture.retirements.pending_slot(fixture.root, "dev")
        assert fixture.runtime.read_dev_claim(fixture.root) == fixture.claim
        assert fixture.runtime._leases.read_runtime_references(fixture.root)


@pytest.mark.parametrize("state", ["cancelled", "retiring", "prepared"])
def test_dev_release_reconciles_exited_resident_before_retry(
    modes, retired_mode_runtime, monkeypatch, state,
):
    fixture = retired_mode_runtime
    fixture.reserve(state, True)
    settlements = []
    monkeypatch.setattr(
        modes.lifecycle.engine, "run_owned",
        lambda *_args, **_kwargs: SimpleNamespace(stdout='"1.0.0"'),
    )

    def settle(root, version, **kwargs):
        assert version == "original"
        assert kwargs["release_dev_claim"] == fixture.claim
        assert kwargs["cutover_guard"].held
        assert not fixture.retirements.pending_slot(root, "dev")
        settlements.append(True)
        fixture.runtime.activate(root, version, link_free=True)
        return {"runtime_version": version}

    monkeypatch.setattr(modes.lifecycle, "settle_candidate", settle)
    if state == "prepared":
        # Preserve the existing exact precommit cancellation retry protocol.
        fixture.runtime.activate(fixture.root, "dev", link_free=True)
    assert fixture.release() == {
        "runtime_version": "original", "reused": True,
        **({"dev_claim_release_pending": False} if state == "retiring" else {}),
    }
    assert settlements == ([] if state == "retiring" else [True])
    assert fixture.runtime.read_dev_claim(fixture.root, strict=True) is None
    assert not fixture.retirements.pending_slot(fixture.root, "dev")
    assert not fixture.runtime._leases.read_runtime_references(fixture.root)
    assert (fixture.root / "versions" / "dev").is_dir()


def test_dev_rebuild_preserves_pending_hashed_release_claim(
    modes, retired_mode_runtime, tmp_path, monkeypatch,
):
    fixture = retired_mode_runtime
    generation = "dev-" + "a" * 64
    owner = str(fixture.owner)
    fixture.runtime.mark_complete(
        fixture.root, generation, payload_hash="dev", dev_owner=owner,
    )
    fixture.runtime.activate(fixture.root, generation, link_free=True, dev_owner=owner)
    claim = fixture.runtime.read_dev_claim(fixture.root, strict=True)
    fixture.retirements.reserve(
        fixture.root, dict(pid=101, start="1001", slot=generation, release_claim=claim),
        runtime=fixture.runtime, committed=True,
    )
    fixture.runtime.activate(fixture.root, "original", link_free=True)
    monkeypatch.setattr(
        fixture.runtime, "claim_dev",
        lambda *_args, **_kwargs: pytest.fail("Pending release claim was overwritten"),
    )
    with pytest.raises(RuntimeError, match="previous dev resident is still retiring"):
        modes.run_owned(
            fixture.root, fixture.plugin, payload=fixture.plugin, uv=tmp_path / "uv",
            base_python=Path(sys.executable), mode="dev", owner=fixture.owner,
            expected_governance={},
        )
    assert fixture.runtime.read_dev_claim(fixture.root, strict=True) == claim
    assert fixture.retirements.pending_claim(fixture.root, claim)
    assert fixture.runtime._leases.read_runtime_references(fixture.root)


@pytest.mark.parametrize("exited", [False, None])
def test_dev_release_preserves_live_or_unknown_retirement(
    retired_mode_runtime, exited,
):
    fixture = retired_mode_runtime
    fixture.reserve("retiring", exited)
    message = "native identity" if exited is None else "pending resident retirement"
    with pytest.raises(RuntimeError, match=message):
        fixture.release()
    assert fixture.runtime.read_dev_claim(fixture.root) == fixture.claim
    assert fixture.retirements.pending_claim(fixture.root, fixture.claim)
    assert fixture.runtime._leases.read_runtime_references(fixture.root)


@pytest.mark.parametrize("same_owner", [False, True])
def test_dev_release_reconciliation_preserves_changed_claim(
    retired_mode_runtime, same_owner,
):
    fixture = retired_mode_runtime
    fixture.reserve("retiring", True)
    changed = fixture.runtime.claim_dev(
        fixture.root, str(fixture.owner) if same_owner else "another-owner",
        host="changed-host", previous_version="original", force=not same_owner,
    )
    message = "Dev claim changed" if same_owner else "dev slot is already claimed"
    with pytest.raises(RuntimeError, match=message):
        fixture.release()
    assert fixture.runtime.read_dev_claim(fixture.root) == changed
    assert fixture.retirements.pending_claim(fixture.root, fixture.claim)
    assert fixture.runtime._leases.read_runtime_references(fixture.root)


def test_dev_release_keeps_exited_uncommitted_intent_after_selector_changed(
    retired_mode_runtime,
):
    fixture = retired_mode_runtime
    fixture.reserve("prepared", True)
    with pytest.raises(RuntimeError, match="pending resident retirement"):
        fixture.release()
    assert fixture.runtime.read_dev_claim(fixture.root) == fixture.claim
    assert fixture.retirements._records(fixture.root)[0][1]["state"] == "prepared"
    assert fixture.runtime._leases.read_runtime_references(fixture.root)


def test_dev_release_rereads_claim_after_reconciliation(retired_mode_runtime, monkeypatch):
    fixture = retired_mode_runtime
    fixture.reserve("cancelled", True)
    reconcile = fixture.retirements.reconcile
    changed = []

    def reconcile_and_reclaim(root, **kwargs):
        result = reconcile(root, **kwargs)
        changed.append(fixture.runtime.claim_dev(
            root, str(fixture.owner), host="new-claim-host",
        ))
        return result

    monkeypatch.setattr(fixture.retirements, "reconcile", reconcile_and_reclaim)
    with pytest.raises(RuntimeError, match="Dev claim changed before its settled release"):
        fixture.release()
    assert fixture.runtime.read_dev_claim(fixture.root) == changed[0]
    assert not fixture.retirements.pending_slot(fixture.root, "dev")


def test_dev_candidate_uses_immutable_content_and_retains_it(modes, tmp_path, monkeypatch):
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
        assert lifecycle.runtime.is_dev_generation(slot.name) and not refresh
        assert slot.name == f"dev-{inputs.payload_hash}"
        assert not lifecycle.runtime.marker_path(root, slot.name).exists()
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
    monkeypatch.setattr(
        lifecycle.engine, "retained_source_wheels", lambda inputs, *_args, **_kwargs: nullcontext(inputs),
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
    snapshots = {}
    for source in ("first", "second"):
        (plugin / "source.py").write_text(source, encoding="utf-8")
        with lifecycle.engine.dev_candidate(root, owner=owner, **arguments) as candidate:
            hashes.append(candidate.payload_hash)
            snapshots[candidate.slot] = {
                path.relative_to(candidate.slot): path.read_bytes()
                for path in candidate.slot.rglob("*") if path.is_file()
            }
            assert candidate.reused is False
            assert (candidate.slot / "observed-source").read_text() == source
            with pytest.raises(lifecycle.runtime.RetentionTimeout):
                with lifecycle.runtime._kernel_lease(
                    root,
                    candidate.slot.name,
                    kind="retention",
                    shared=False,
                    timeout=0,
                ):
                    pytest.fail("dev candidate was not retained")
            with lifecycle.runtime.construction_lease(root, candidate.slot.name, timeout=0):
                pass
    assert constructions == ["first", "second"] and hashes[0] != hashes[1]
    for slot, snapshot in snapshots.items():
        assert {
            path.relative_to(slot): path.read_bytes()
            for path in slot.rglob("*") if path.is_file()
        } == snapshot
    with lifecycle.engine.dev_candidate(root, owner=owner, **arguments) as reused:
        assert reused.reused and reused.slot in snapshots
    assert constructions == ["first", "second"]
    assert lifecycle.runtime.read_dev_claim(root)["previous_version"] == "numbered"
    marker_path = lifecycle.runtime.marker_path(root, reused.slot.name)
    marker = marker_path.read_bytes()
    with pytest.raises(RuntimeError, match="explicit dev claim"):
        with lifecycle.engine.dev_candidate(root, owner="other", **arguments):
            pytest.fail("non-owner constructed dev")
    assert marker_path.read_bytes() == marker
    marker_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(lifecycle.engine.HealthRepairRequired):
        with lifecycle.engine.dev_candidate(root, owner=owner, **arguments):
            pytest.fail("broken published development content was rebuilt")
    assert marker_path.read_text() == "{broken" and constructions == ["first", "second"]
    assert not (root / "versions" / "dev").exists()


@pytest.mark.parametrize("settlement_failure", [False, True])
@pytest.mark.parametrize("retirement_pending,retiring_dev,already_released", [
    (False, False, False), (True, False, False), (True, True, False),
    (False, True, True),
])
@pytest.mark.parametrize("previous", ["original", "dev-" + "b" * 64])
def test_dev_release_keeps_original_claim_until_restoration_settles(
    modes,
    tmp_path,
    monkeypatch,
    settlement_failure,
    retirement_pending,
    retiring_dev,
    already_released,
    previous,
):
    from agent_worktrees import status_monitor_cutover as cutover

    lifecycle = modes.lifecycle
    root, plugin = tmp_path / "runtime", tmp_path / "plugin"
    slot = root / "versions" / previous
    slot.mkdir(parents=True)
    owner = tmp_path / "owner"
    lifecycle.runtime.claim_dev(root, str(owner), previous_version=previous)
    lifecycle.runtime.mark_complete(
        root, previous, payload_hash="original-content", dev_owner=str(owner),
    )
    (root / "versions" / "dev").mkdir()
    lifecycle.runtime.mark_complete(root, "dev", payload_hash="dev-content")
    lifecycle.runtime.activate(root, "dev", link_free=True)
    events = []

    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload
            self.active = False

        def signature(self):
            return {}

        @contextmanager
        def held(self, *, timeout=180):
            if self.active:
                yield
                return
            self.active = True
            events.append("enter-governance")
            try:
                yield
            finally:
                events.append("leave-governance")
                self.active = False

    guard = SimpleNamespace(release=lambda: events.append("release-cutover"))

    def settle(requested, version, **kwargs):
        assert version == previous and kwargs["previous"] == "dev"
        assert kwargs["cutover_guard"] is guard
        assert kwargs["payload_hash"] == "original-content"
        assert kwargs["package_version"] == "1.0.0"
        assert events == ["enter-governance"]
        assert kwargs["governance"].active
        assert kwargs["release_dev_claim"] == lifecycle.runtime.read_dev_claim(root)
        assert kwargs["dev_owner"] == str(owner)
        assert lifecycle.runtime.read_dev_claim(root)["previous_version"] == previous
        for retained in (previous, "dev"):
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
        lifecycle.runtime.activate(root, version, link_free=True, dev_owner=kwargs["dev_owner"])
        if already_released:
            lifecycle.runtime.release_dev(root, str(owner))
        return {
            "runtime_version": version, "retirement_pending": retirement_pending,
            "dev_claim_release_pending": retirement_pending and retiring_dev,
        }

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
        result = modes.run_owned(root, plugin, **arguments)
        assert result["reused"] is True
        if retirement_pending and retiring_dev:
            assert result["dev_claim_release_pending"] is True
            assert lifecycle.runtime.read_dev_claim(root)["owner"] == str(owner)
        else:
            assert lifecycle.runtime.read_dev_claim(root) is None
        assert lifecycle.runtime.current_version(root) == previous
    assert events[-2:] == ["release-cutover", "leave-governance"]


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
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        PSModuleAnalysisCachePath=str(home / "module-analysis.cache"),
    )
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
                        "-BootstrapPython",
                        sys.executable,
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
        try:
            result = run_native_installer(argv, env=environment, timeout=480)
        except subprocess.TimeoutExpired as error:
            log.write_text((error.stdout or "") + (error.stderr or ""), encoding="utf-8")
            pytest.fail("Native repair did not settle:\n" + log.read_text(encoding="utf-8"))
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
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


@pytest.mark.parametrize("previous", ["1.2.3", "dev-" + "b" * 64])
def test_development_claim_warning_keeps_restoration_owned(tmp_path, monkeypatch, capsys, previous):
    import shlex

    from agent_worktrees.finalize import _warn_of_dev_slot_claims_for_worktree

    owner = str(tmp_path / "owner's worktree")
    root = tmp_path / ".agent-worktrees"
    root.mkdir()
    claim = root / "dev-claim.json"
    claim.write_text(json.dumps({
        "schema": "copilot-extensions.dev-slot-claim",
        "owner": owner,
        "previous_version": previous,
    }), encoding="utf-8")
    original = claim.read_bytes()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    _warn_of_dev_slot_claims_for_worktree("owner-worktree", owner)
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert previous in text
    assert "-Owner '" + owner.replace("'", "''") + "'" in text
    assert "--owner " + shlex.quote(owner) in text
    assert "-InstallDir '" + str(root).replace("'", "''") + "'" in text
    assert "--install-dir " + shlex.quote(str(root)) in text
    assert "keep the claim until restoration settles" in text.lower()
    assert "delete ~/" not in text
    assert "versioned_runtime.py activate" not in text
    assert claim.read_bytes() == original
