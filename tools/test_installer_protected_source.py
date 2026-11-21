"""Inert ownership controls do not establish native privileged qualification."""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Iterator
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs/installer-engine"))
import installer_protected_source as protected
import pytest
from test_local_install_source import (
    application_adapter as application_adapter,
    prepare_native_source,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux protected-provider source controls"
)


def _elf_image(loader: Path | None = None, *, wide: bool = True) -> bytes:
    size, width = (64, 56) if wide else (52, 32)
    content = bytearray(size + width)
    content[:16] = b"\x7fELF" + bytes((2 if wide else 1, 1, 1)) + bytes(9)
    struct.pack_into("<HHI", content, 16, 2, 62 if wide else 3, 1)
    struct.pack_into("<Q" if wide else "<I", content, 32 if wide else 28, size)
    struct.pack_into("<HHH", content, 52 if wide else 40, size, width, 1)
    struct.pack_into("<I", content, size, 1 if loader is None else 3)
    if loader is not None:
        encoded = str(loader).encode("ascii") + b"\0"
        struct.pack_into(
            "<Q" if wide else "<I", content, size + (8 if wide else 4), len(content)
        )
        struct.pack_into(
            "<Q" if wide else "<I", content, size + (32 if wide else 16), len(encoded)
        )
        content.extend(encoded)
    return bytes(content)


@pytest.mark.parametrize(
    "provider", ["current", "old", "malformed", "rolled-back", "old-entry"]
)
def test_required_contract_is_read_from_selected_owner_without_executing_code(
    tmp_path: Path,
    provider: str,
) -> None:
    declaration = dict(protected.PROTECTED_INSTALLATION_CONTRACT)
    if provider == "rolled-back":
        declaration["version"] = 0
    helper = tmp_path / "installer_protected_source.py"
    helper.write_text(
        "PROTECTED_INSTALLATION_CONTRACT = (\n"
        if provider == "malformed"
        else "pass\n"
        if provider == "old"
        else "PROTECTED_INSTALLATION_CONTRACT = " + repr(declaration) + "\n"
        "raise AssertionError('capability discovery executed provider code')\n"
    )
    entry = tmp_path / "bootstrap-protected-install.sh"
    entry.write_bytes(
        b"not executed"
        if provider == "old-entry"
        else b"#!/bin/bash -p\n# protected-installation-provider: linux-root/v1\n"
        b"exit 99 # discovery must never execute this entry\n"
    )
    if provider == "current":
        assert protected.select_entry(tmp_path) == entry
    else:
        with pytest.raises(ValueError, match="lacks the protected"):
            protected.select_entry(tmp_path)


@pytest.fixture
def administrative_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Model native metadata only; never change the executing process identity."""
    original = Path.lstat

    def info(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        fields = list(original(path, *args, **kwargs))
        fields[4] = 1001 if path.name == "foreign" else 0
        if path.name == "owned-socket":
            fields[0] = stat.S_IFSOCK | 0o660
        if path == tmp_path or path in tmp_path.parents:
            fields[0] = int(fields[0]) & ~0o022
        return os.stat_result(fields)

    monkeypatch.setattr(protected, "_metadata", info)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(protected, "_root_identity", lambda: True)
    mounts = tmp_path / "mountinfo"
    mounts.write_bytes(b"1 0 0:1 / / rw - ext4 /dev/fixture rw\n")
    monkeypatch.setattr(protected, "_MOUNTINFO", mounts)

    def no_default_acl(*_args: Any, **_kwargs: Any) -> bytes:
        raise OSError(errno.ENODATA, "inert absent ACL")

    monkeypatch.setattr(os, "getxattr", no_default_acl)


@pytest.fixture
def policy(
    tmp_path: Path, administrative_metadata: None
) -> tuple[Path, dict[str, Any]]:
    code = tmp_path / "provider"
    code.mkdir(mode=0o700)
    native_helper = code / "installer_native_executable.py"
    native_helper.write_bytes(
        Path(protected.__file__).with_name(native_helper.name).read_bytes()
    )
    native_helper.chmod(0o600)
    record: dict[str, Any] = {
        "contract": dict(protected.PROTECTED_INSTALLATION_CONTRACT),
        "component": "demo-app",
        "health_module": "demo_app",
        "expected_version": "1.0.profile.fixture",
        "expected_hash": "a" * 64,
        "arguments": ["--setup-value"],
        "repair": False,
        "global_bin": None,
    }
    for name in ("snapshot", "root", "home", "cache", "temporary"):
        selected = tmp_path / ("selected-" + name)
        selected.mkdir(mode=0o700)
        record[name] = str(selected)
    uv = tmp_path / "uv"
    uv.write_bytes(_elf_image())
    uv.chmod(0o700)
    record["uv"] = str(uv)
    path = code / "protected-installation.json"
    path.write_text(json.dumps(record))
    path.chmod(0o600)
    return path, record


def _admit(policy: tuple[Path, dict[str, Any]]) -> protected.ProtectedApplication:
    path, record = policy
    path.write_text(json.dumps(record))
    return protected.admit(path, code=path.parent)


def test_admission_retains_addressed_inputs_without_requiring_incumbent_receipts(
    policy: tuple[Path, dict[str, Any]],
) -> None:
    _, record = policy
    root = Path(record["root"])
    (root / "current").symlink_to(root / "versions" / "missing-incumbent")
    (root / "retained").write_bytes(b"preserved")
    (root / "retained").chmod(0o600)
    selected = _admit(policy)
    assert selected.root == root
    assert selected.arguments == ("--setup-value",)
    assert selected.snapshot == Path(record["snapshot"])
    assert (root / "retained").read_bytes() == b"preserved"
    assert sorted(path.name for path in root.iterdir()) == ["current", "retained"]


@pytest.mark.parametrize("role", ["root", "snapshot"])
def test_output_ipc_is_preserved_but_cannot_supply_executable_inputs(
    policy: tuple[Path, dict[str, Any]],
    role: str,
) -> None:
    _, record = policy
    placeholder = Path(record[role]) / "owned-socket"
    placeholder.write_bytes(b"inert socket metadata model; no IPC created")
    if role == "root":
        assert _admit(policy).root == Path(record["root"])
        assert (
            placeholder.read_bytes() == b"inert socket metadata model; no IPC created"
        )
    else:
        with pytest.raises(ValueError, match="replaceable content"):
            _admit(policy)


@pytest.mark.parametrize(
    "case",
    [
        "old-provider",
        "malformed-version",
        "extra-field",
        "unknown-operation",
        "relative-root",
        "root-traversal",
        "root-is-filesystem",
        "tool-not-executable",
        "changed-owner",
        "writable-code",
        "linked-source",
        "escaping-incumbent-link",
        "indirect-parent-link",
        "input-output-overlap",
        "output-role-overlap",
        "writable-command",
    ],
)
def test_invalid_selections_refuse_before_construction(
    policy: tuple[Path, dict[str, Any]],
    case: str,
) -> None:
    path, record = policy
    if case == "old-provider":
        record["contract"]["provider"] = "linux-root/v0"
    elif case == "malformed-version":
        record["contract"]["version"] = True
    elif case == "unknown-operation":
        record["contract"]["operation"] = "execute-controller"
    elif case == "extra-field":
        record["approved"] = True
    elif case == "relative-root":
        record["root"] = "relative"
    elif case == "root-traversal":
        record["root"] += "/../root"
    elif case == "root-is-filesystem":
        record["root"] = "/"
    elif case == "tool-not-executable":
        Path(record["uv"]).chmod(0o600)
    elif case == "changed-owner":
        (Path(record["snapshot"]) / "foreign").write_bytes(b"unadmitted")
    elif case == "writable-code":
        path.parent.chmod(0o770)
    elif case == "linked-source":
        (Path(record["snapshot"]) / "linked").symlink_to(record["uv"])
    elif case == "escaping-incumbent-link":
        (Path(record["root"]) / "current").symlink_to(record["snapshot"])
    elif case == "indirect-parent-link":
        (Path(record["root"]) / "current").symlink_to("nested/../versions")
    elif case == "input-output-overlap":
        record["root"] = record["snapshot"]
    elif case == "writable-command":
        commands = path.parent.parent / "commands"
        commands.mkdir(mode=0o700)
        (commands / "demo-app").write_bytes(b"already matching command bytes")
        (commands / "demo-app").chmod(0o660)
        record["global_bin"] = str(commands)
    else:
        record["cache"] = record["home"]
    before = sorted(Path(record["snapshot"]).iterdir())
    with pytest.raises(ValueError):
        _admit(policy)
    assert sorted(Path(record["snapshot"]).iterdir()) == before


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_unsupported_platform_refuses_before_reading_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(ValueError, match="Linux root identity only"):
        protected.admit(tmp_path / "absent", code=tmp_path)


def test_duplicate_policy_fields_and_withdrawn_policy_are_not_approval(
    policy: tuple[Path, dict[str, Any]],
) -> None:
    path, _ = policy
    path.write_text('{"contract": {}, "contract": {}}')
    with pytest.raises(ValueError, match="duplicate"):
        protected.admit(path, code=path.parent)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        protected.admit(path, code=path.parent)


def test_admission_inspection_keeps_its_finite_bound(
    policy: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = policy
    monkeypatch.setattr(protected, "_MAX_ENTRIES", 0)
    with pytest.raises(ValueError, match="inspection bound"):
        protected.admit(path, code=path.parent)


@pytest.mark.parametrize(
    "filesystem", ["9p", "drvfs", "nfs4", "fuse", "cifs", "overlay"]
)
def test_native_mode_emulation_is_not_protected_custody(
    policy: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    filesystem: str,
) -> None:
    _, record = policy
    with (tmp_path / "mountinfo").open("ab") as stream:
        stream.write(
            f"2 1 0:2 / {record['snapshot']} rw - {filesystem} fixture rw\n".encode(),
        )
    with pytest.raises(ValueError, match="native Linux filesystems"):
        _admit(policy)


@pytest.mark.parametrize("role", ["root", "home", "cache", "temporary", "global_bin"])
@pytest.mark.parametrize("nested", [False, True])
def test_output_default_acl_refuses_before_creating_children(
    policy: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    nested: bool,
) -> None:
    _, record = policy
    if role == "global_bin":
        record[role] = record["root"]
    directory = Path(record[role])
    if nested:
        directory /= "nested"
        directory.mkdir(mode=0o700)

    def inherited_acl(path: Path, *_args: Any, **_kwargs: Any) -> bytes:
        if path == directory:
            return b"inert default ACL granting controller write"
        raise OSError(errno.ENODATA, "inert absent ACL")

    monkeypatch.setattr(os, "getxattr", inherited_acl)
    with pytest.raises(ValueError, match="default ACL"):
        _admit(policy)
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("cause", [errno.EACCES, errno.EOPNOTSUPP])
def test_unverifiable_output_acl_refuses(
    policy: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    cause: int,
) -> None:
    def unavailable(*_args: Any, **_kwargs: Any) -> bytes:
        raise OSError(cause, "inert unavailable ACL inspection")

    monkeypatch.setattr(os, "getxattr", unavailable)
    with pytest.raises(ValueError, match="cannot be inspected"):
        _admit(policy)


@pytest.mark.parametrize("alias", ["input", "output", "nested", "stacked"])
def test_mount_aliases_cannot_overlap_lexically_disjoint_roles(
    policy: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    alias: str,
) -> None:
    _, record = policy
    root = Path(record["root"])
    origin = Path(record["snapshot"] if alias == "input" else record["home"])
    mount = root / "nested" if alias == "nested" else root
    if alias == "nested":
        mount.mkdir()
    line = f"2 1 0:1 {origin} {mount} rw - ext4 /dev/fixture rw\n"
    with (tmp_path / "mountinfo").open("a") as stream:
        stream.write(line + (line if alias == "stacked" else ""))
    with pytest.raises(ValueError, match="overlap|disjoint|nested|ambiguous"):
        _admit(policy)
    assert not (root / "versions").exists()


@pytest.mark.parametrize(
    "fault",
    [None, "foreign", "writable", "escape", "loop", "prefix", "prefix-chain"],
)
def test_os_interpreter_chain_checks_every_link_and_directory(
    policy: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    _, record = policy
    prefix = tmp_path / "os-prefix"
    prefix.mkdir(mode=0o700)
    directory = prefix / ("foreign" if fault == "foreign" else "bin")
    directory.mkdir(mode=0o700)
    if fault == "writable":
        directory.chmod(0o777)
    interpreter = directory / "python3.12"
    interpreter.write_bytes(b"inert physical OS executable")
    interpreter.chmod(0o500)
    link = prefix / "python3"
    link.symlink_to(
        link
        if fault == "loop"
        else (
            Path(record["uv"])
            if fault == "escape"
            else prefix
            if fault == "prefix-chain"
            else interpreter
        )
    )
    (Path(record["root"]) / "retained-python").symlink_to(
        prefix if fault == "prefix" else link,
    )
    monkeypatch.setattr(sys, "base_prefix", str(prefix))
    if fault is None:
        assert _admit(policy).root == Path(record["root"])
    else:
        with pytest.raises(ValueError, match="interpreter"):
            _admit(policy)


def test_existing_os_interpreter_link_does_not_require_incumbent_provenance(
    policy: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, record = policy
    operating_system = tmp_path / "operating-system"
    operating_system.mkdir(mode=0o700)
    interpreter = operating_system / "python"
    interpreter.write_bytes(b"inert OS interpreter reference")
    interpreter.chmod(0o500)
    monkeypatch.setattr(sys, "base_prefix", str(operating_system))
    mount_reads: list[tuple[Path, ...]] = []
    original = protected._native_mounts

    def observe(paths: tuple[Path, ...]) -> dict[Path, tuple[bytes, Path]]:
        mount_reads.append(paths)
        return original(paths)

    monkeypatch.setattr(protected, "_native_mounts", observe)
    for index in range(10):
        (Path(record["root"]) / f"retained-python-{index}").symlink_to(interpreter)
    assert _admit(policy).root == Path(record["root"])
    assert len(mount_reads) == 2, "mount inspection must not repeat for every OS link"


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "changed-selection",
        "old-launcher",
        "script-tool",
        "callback-context",
    ],
)
def test_main_keeps_real_prepared_source_and_admitted_selections_through_construction(
    policy: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str | None,
) -> None:
    from runtime_application_project import emit

    source, snapshot = prepare_native_source(tmp_path / "native-source", request)
    path, record = policy
    application = snapshot / "application"
    with source.project_inputs(snapshot, "demo-app") as (project, inputs):
        selection = source.native_selection(project, inputs)
        emit(
            application,
            distribution="demo-app",
            version="1.0.0",
            requires_python=">=3.11",
            commands={"demo-app": "demo_app:main"},
            setup=[],
            start=[],
            wheels={
                "demo-app": snapshot
                / project.project["wheels"]
                / inputs.distributions[0][2]
            },
            runtime_source=project.identity.document(),
            construction=source.application_inputs(project, inputs),
        )
    manifest = snapshot / source.MANIFEST
    document = json.loads(manifest.read_text())
    document["projects"]["demo-app"]["application"] = "application"
    manifest.write_text(json.dumps(document))
    for member in (snapshot, *snapshot.parents):
        if member.is_relative_to(tmp_path):
            member.chmod(member.stat().st_mode & ~0o022)
    for member in snapshot.rglob("*"):
        member.chmod(member.stat().st_mode & ~0o022)
    record.update(
        snapshot=str(snapshot),
        expected_version=selection["runtime_version"],
        expected_hash="b" * 64
        if fault == "changed-selection"
        else selection["payload_hash"],
    )
    if fault == "old-launcher":
        (application / "install.sh").write_text('#!/bin/sh\nexec uv "$@"\n')
    if fault == "script-tool":
        Path(record["uv"]).write_bytes(b"#!/controller/writable-python\npass\n")
    if fault == "callback-context":
        declaration = application / "application.json"
        values = json.loads(declaration.read_bytes())
        values.update(
            name="agent-worktrees",
            commands=["agent-worktrees"],
            start=["__source-start"],
        )
        declaration.write_text(json.dumps(values))
    path.write_text(json.dumps(record))
    (path.parent / "versioned_runtime.py").write_text(
        "# inert packaged-family presence\n"
    )
    (path.parent / "versioned_runtime.py").chmod(0o600)
    admitted = protected.admit(path, code=path.parent)
    calls: list[dict[str, Any]] = []
    engine = source.engine

    def child(argv: list[str], **options: Any) -> None:
        slot = options["candidate"]
        calls.append({"argv": argv, **options})
        if "--stage" in argv:
            environment = options["environment"]
            assert environment["PATH"] == "/usr/bin:/bin"
            assert environment["COPILOT_INSTALLER_UV"] == str(admitted.uv)
            assert environment["HOME"] == str(admitted.home)
            assert environment["TMPDIR"] == str(admitted.temporary)
            assert environment["UV_CACHE_DIR"] == str(admitted.cache / "uv")
            directory = slot / ".venv/bin"
            directory.mkdir(parents=True)
            (directory / "python").write_bytes(b"inert constructor output")
            (slot / ".venv/pyvenv.cfg").write_bytes(b"inert constructor output")
        else:
            (slot / "install-success.json").write_text(
                json.dumps(
                    {
                        "state": "installed",
                        "version": slot.name,
                    }
                )
            )

    @contextmanager
    def candidate(root: Path, version: str, **options: Any) -> Iterator[Any]:
        assert root == admitted.root and options["uv"] == admitted.uv
        assert version == admitted.expected_version
        packages = engine._package_roots(options["plugin"])
        frozen = engine.FrozenInputs(
            options["plugin"],
            packages,
            selection["payload_hash"],
            retained_hash=engine.runtime.fingerprint_source(
                engine._source_files(packages)
            ),
            extras=tuple(selection["extras"]),
            local_packages=tuple(selection["local_packages"]),
            wheel_inputs=options["wheel_inputs"],
            application=options["application"],
        )
        slot = root / "versions" / version
        slot.mkdir(parents=True)
        engine._construct(frozen, slot, admitted.uv, options["base_python"], None, 30)
        options["validate_candidate"](slot)
        yield engine.ReadyCandidate(
            slot, selection["payload_hash"], False, None, frozen.plugin
        )

    monkeypatch.setattr(engine, "numbered_candidate", candidate)
    monkeypatch.setattr(engine, "_child", child)
    monkeypatch.setitem(sys.modules, "install_source", source)
    monkeypatch.setattr(sys, "path", sys.path[:])
    monkeypatch.setattr(sys, "argv", ["protected-provider"])
    flags = SimpleNamespace(
        **{
            name: getattr(sys.flags, name)
            for name in dir(sys.flags)
            if not name.startswith("_")
        }
    )
    flags.isolated = flags.no_site = True
    monkeypatch.setattr(sys, "flags", flags)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(
        protected, "__file__", str(path.parent / "installer_protected_source.py")
    )
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.chdir(tmp_path)
    if fault:
        with pytest.raises(
            ValueError,
            match="selection changed|absolute UV|native ELF|callback context",
        ):
            protected.main()
        assert calls == []
        assert not (admitted.root / "versions").exists()
    else:
        assert protected.main() == 0
        assert json.loads(capsys.readouterr().out) == {
            "state": "installed",
            "slot": str(admitted.root / "versions" / admitted.expected_version),
        }
        assert len(calls) == 2
        assert (
            source.select_project(snapshot, admitted.component).payload
            == project.payload
        )


def test_generated_install_entry_executes_the_exact_renamed_uv_without_sibling_lookup(
    tmp_path: Path,
) -> None:
    from runtime_application_binstubs import slot_binstubs

    tools = tmp_path / "tools"
    tools.mkdir()
    uv = tools / "admitted-executable"
    uv.write_text('#!/bin/sh\nprintf \'%s\\n\' "$0" "$@"\n')
    uv.chmod(0o700)
    for name in ("uv", "dirname", "cygpath"):
        (tools / name).write_text(
            "#!/bin/sh\necho 'unadmitted sibling executed' >&2\nexit 91\n"
        )
        (tools / name).chmod(0o700)
    entry = tmp_path / "install.sh"
    entry.write_text(slot_binstubs("demo-app", "demo_app")["install.sh"])
    result = subprocess.run(
        ["/bin/sh", str(entry), "--stage"],
        env={"PATH": "/usr/bin:/bin", "COPILOT_INSTALLER_UV": str(uv)},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == str(uv)
    assert result.stdout.splitlines()[-1] == "--stage"
    assert result.stderr == ""


@pytest.mark.parametrize("declared", [False, True])
def test_uv_capability_inspection_reads_only_its_prefix_without_a_legacy_size_limit(
    tmp_path: Path,
    declared: bool,
) -> None:
    import installer_engine

    prefix = b"#!/bin/sh\n"
    if declared:
        prefix += b"# installer-uv-selection: absolute/v1\n"
    (tmp_path / "install.sh").write_bytes(prefix + b"# legacy content\n" * 8192)
    assert installer_engine.application_uv_selection(tmp_path) is declared


@pytest.mark.parametrize(
    "case",
    [
        "static64",
        "static32",
        "protected-loader",
        "foreign-loader",
        "dynamic-section",
        "script",
        "bad-layout",
    ],
)
def test_uv_selection_refuses_replaceable_interpreters_and_non_native_inputs(
    tmp_path: Path,
    administrative_metadata: None,
    case: str,
) -> None:
    from installer_native_executable import require_native_executable

    loader = tmp_path / ("foreign" if case == "foreign-loader" else "os-loader")
    loader.write_bytes(b"inert loader reference")
    loader.chmod(0o500)
    uv = tmp_path / "native-uv"
    content = _elf_image(
        loader if case.endswith("loader") else None,
        wide=case != "static32",
    )
    if case == "script":
        content = b"#!" + str(loader).encode() + b"\ncontroller code\n"
    elif case == "bad-layout":
        content = content[:32]
    elif case == "dynamic-section":
        dynamic = bytearray(content)
        struct.pack_into("<I", dynamic, 64, 2)
        content = bytes(dynamic)
    uv.write_bytes(content)
    if case not in {"static64", "static32"}:
        with pytest.raises(ValueError, match="static ELF|native executable|native ELF"):
            require_native_executable(uv)
    else:
        require_native_executable(uv)


@pytest.mark.parametrize(
    "outcome",
    [
        "installed",
        "changed-inputs",
        "outside-package",
        "constructor-collision",
        "unknown-start",
    ],
)
def test_protected_dispatch_reaches_actual_application_adapter_without_replay(
    application_adapter: SimpleNamespace,
    policy: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    fixture = application_adapter
    source = fixture.source
    snapshot = fixture.application.parent
    source.select_project(snapshot, "demo-app").payload = snapshot / "payload"
    (fixture.application / "install.sh").write_bytes(
        b"#!/bin/sh\n# installer-uv-selection: absolute/v1\n"
    )
    selected = replace(
        _admit(policy),
        snapshot=snapshot,
        root=fixture.root,
        expected_version=fixture.selection["runtime_version"],
        expected_hash=fixture.selection["payload_hash"],
    )
    monkeypatch.setitem(sys.modules, "install_source", source)
    monkeypatch.setattr(sys, "path", sys.path[:])
    if outcome == "changed-inputs":
        selected = replace(selected, expected_hash="b" * 64)
        with pytest.raises(ValueError, match="selection changed"):
            protected.install(selected, code=policy[0].parent)
        assert fixture.versions == []
        assert fixture.children == []
    elif outcome == "outside-package":
        monkeypatch.setattr(source.engine, "_package_roots", lambda *_: (fixture.root,))
        with pytest.raises(ValueError, match="closure leaves"):
            protected.install(selected, code=policy[0].parent)
        assert fixture.versions == fixture.children == []
    elif outcome == "constructor-collision":
        collision = FileExistsError("retained candidate collision")
        fixture.failures.append(collision)
        with pytest.raises(FileExistsError) as collision_result:
            protected.install(selected, code=policy[0].parent)
        assert collision_result.value is collision
        assert fixture.versions == [fixture.old.name]
        assert fixture.children == []
    elif outcome == "unknown-start":
        error = RuntimeError("application-owned uncertain effect")
        fixture.child_error = error
        with pytest.raises(RuntimeError) as caught:
            protected.install(selected, code=policy[0].parent)
        assert caught.value is error
        assert len(fixture.versions) == len(fixture.children) == 1
    else:
        assert protected.install(selected, code=policy[0].parent) == {
            "state": "installed",
            "slot": str(fixture.old),
        }
        assert fixture.versions == [fixture.old.name]
        assert fixture.candidate_options[0]["uv"] == selected.uv
    assert (fixture.old / "preserved").read_bytes() == b"incumbent bytes"


@pytest.mark.parametrize("closed", [False, True])
def test_protected_package_guard_preserves_closed_source_development_files(
    policy: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    closed: bool,
) -> None:
    from installer_dependency_policy import DependencyPolicyError
    from installer_engine_health import application_uv_selection
    from installer_source_build import _package_roots

    selected = _admit(policy)
    payload = selected.snapshot / "payload"
    payload.mkdir()
    metadata = (
        '[project]\nname="demo-app"\nversion="1.0"\n'
        '[tool.uv.sources]\nlegacy={path="legacy.whl"}\n'
    ).encode()
    (payload / "pyproject.toml").write_bytes(metadata)
    (payload / "legacy.whl").write_bytes(b"development-only wheel")
    app = selected.snapshot / "application"
    app.mkdir()
    (app / "install.sh").write_bytes(
        b"#!/bin/sh\n# installer-uv-selection: absolute/v1\n"
    )
    (app / "application.json").write_text('{"name":"demo-app"}')
    source = ModuleType("install_source")
    calls: list[dict[str, Any]] = []

    def install(*_args: Any, **options: Any) -> dict[str, str]:
        calls.append(options)
        return {"state": "installed"}

    vars(source).update(
        select_project=lambda *_: SimpleNamespace(
            payload=payload,
            project={"application": "application"},
            closed_wheel_source=closed,
        ),
        engine=SimpleNamespace(
            _package_roots=_package_roots,
            application_uv_selection=application_uv_selection,
        ),
        _member=lambda root, name: root / name,
        _read=lambda path: json.loads(path.read_bytes()),
        install_application=install,
    )
    monkeypatch.setitem(sys.modules, "install_source", source)
    monkeypatch.setattr(sys, "path", sys.path[:])
    if closed:
        assert (
            protected.install(selected, code=policy[0].parent)["state"] == "installed"
        )
        assert len(calls) == 1
    else:
        with pytest.raises(DependencyPolicyError, match="release age"):
            protected.install(selected, code=policy[0].parent)
        assert calls == []
    assert (payload / "pyproject.toml").read_bytes() == metadata
    assert (payload / "legacy.whl").read_bytes() == b"development-only wheel"


@pytest.mark.skipif(
    sys.platform != "linux" or os.getuid() == 0,
    reason="unprivileged Linux entry control",
)
def test_native_entry_refuses_ordinary_controller_before_loading_python(
    tmp_path: Path,
) -> None:
    entry = Path(protected.__file__).with_name("bootstrap-protected-install.sh")
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "installer_protected_source.py").write_text(
        "raise AssertionError('controller code ran')"
    )
    result = subprocess.run(
        ["/bin/bash", "-p", str(entry)],
        cwd=poison,
        env={"PATH": str(poison), "PYTHONPATH": str(poison)},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "Linux root-owned native entry" in result.stderr
    assert "controller code ran" not in result.stderr
