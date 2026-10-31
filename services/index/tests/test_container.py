"""Synthetic wheels prove offline context contracts without building images."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
from dataclasses import replace

import pytest
import test_release as wheels
from agent_procutil import no_window_kwargs

from agent_index_service import container
from agent_index_service.__main__ import main
from agent_index_service.container import ContainerError, ContainerTarget, prepare_container_context

BASE = "approved.invalid/python@sha256:" + "a" * 64
TARGET = ContainerTarget("3.12.3", "linux/amd64", "2.36")


@pytest.fixture
def case(tmp_path):
    bundle = tmp_path / "bundle"
    third = tmp_path / "third"
    bundle.mkdir()
    third.mkdir()
    for name, version in wheels.VERSIONS.items():
        wheels._make_wheel(bundle, name, version)
    files = {
        "httpx": wheels._make_wheel(third, "httpx", "0.28.0"),
        "pyyaml": wheels._make_wheel(third, "pyyaml", "6.0.3"),
    }
    lock = tmp_path / "third-party.txt"
    lock.write_text("".join(
        f"{name}=={version} --hash=sha256:{hashlib.sha256(files[name].read_bytes()).hexdigest()}\n"
        for name, version in (("httpx", "0.28.0"), ("pyyaml", "6.0.3"))
    ))
    descriptor = wheels._save(bundle, wheels.build_descriptor(bundle, source_commit=wheels.COMMIT))
    return {
        "descriptor_path": descriptor, "expected_source_commit": wheels.COMMIT,
        "third_party_lock": lock, "third_party_wheels": third, "output": tmp_path / "image",
        "base_image": BASE, "target": TARGET,
    }


def prepare(case, **changes):
    values = {**case, **changes}
    descriptor = values.pop("descriptor_path")
    return prepare_container_context(descriptor, **values)


def test_exact_prepared_context_and_no_secret_copy(case):
    (case["third_party_wheels"] / "private-config.json").write_text('{"token":"secret-sentinel"}')
    result = prepare(case)
    assert result["state"] == "prepared"
    output = case["output"]
    contract = json.loads((output / "context.json").read_text())
    assert contract["source_commit"] == wheels.COMMIT
    assert contract["base_image"] == BASE
    assert set(contract["pins"]) == set(wheels.VERSIONS) | {"httpx", "pyyaml"}
    assert contract["extras"]["agent-index-service"] == ["native"]
    assert contract["extras"]["agent-index"] == ["store", "server"]
    actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    assert actual == {item["file"] for item in contract["files"]} | {
        "context.json", "Dockerfile", ".dockerignore",
    }
    assert "private-config.json" not in str(actual)
    assert not any(b"secret-sentinel" in path.read_bytes() for path in output.rglob("*")
                   if path.is_file())
    dockerfile = (output / "Dockerfile").read_text()
    assert dockerfile.count(BASE) == 2
    assert dockerfile.split(" AS build\n", 1)[1].startswith("USER 0\n")
    assert "USER 10001:10001" in dockerfile
    runtime = dockerfile.split(" AS runtime\n", 1)[1]
    assert runtime.startswith("USER 0\n")
    assert "getent passwd 10001" in runtime
    assert "groupadd --gid 10001 agent-index-10001" in runtime
    assert "usermod --gid 10001 --home /var/lib/agent-index" in runtime
    assert "useradd --uid 10001 --gid 10001 --home-dir /var/lib/agent-index" in runtime
    assert "install -d -m 700 -o 10001 -g 10001 /var/lib/agent-index" in runtime
    assert runtime.index("getent passwd") < runtime.index("USER 10001:10001")
    assert "EXPOSE" not in dockerfile and "COPY . " not in dockerfile
    assert '"/opt/agent-index/bin/python", "-I", "-m", "agent_index_service", "serve"' in dockerfile
    assert "RUN --network=none" in dockerfile
    assert "--no-index" in (output / "verify-and-install.py").read_text()
    assert result["build_argv"][:3] == ["agent-index-service", "container", "build"]
    assert {"Dockerfile", ".dockerignore"} <= {
        item["file"] for item in contract["files"]
    }
    assert "ARG CONTEXT_SHA256" in dockerfile
    assert result["contract_sha256"] not in dockerfile  # No circular digest.


def test_replay_unchanged_and_unrelated_output_preserved(case):
    first = prepare(case)
    before = container._files(case["output"])
    assert prepare(case)["state"] == "already_prepared"
    assert container._files(case["output"]) == before
    assert prepare(case)["contract_sha256"] == first["contract_sha256"]
    extra = case["output"] / "unrelated"
    extra.write_text("keep")
    with pytest.raises(ContainerError, match="conflicts"):
        prepare(case)
    assert extra.read_text() == "keep"


@pytest.mark.parametrize("uid,gid", [(10001, 10001), (1001, 1001), (12001, 12002)])
def test_selected_account_profile_matches_numeric_runtime_identity(case, uid, gid):
    prepare(case, uid=uid, gid=gid)
    runtime = (case["output"] / "Dockerfile").read_text().split(" AS runtime\n", 1)[1]
    assert f"getent group {gid}" in runtime
    assert f"getent passwd {uid}" in runtime
    assert f"usermod --gid {gid} --home /var/lib/agent-index" in runtime
    assert f"agent-index-{uid}" in runtime
    assert f"install -d -m 700 -o {uid} -g {gid} /var/lib/agent-index" in runtime
    assert f"USER {uid}:{gid}" in runtime
    assert "test ! -L /var/lib/agent-index" in runtime


@pytest.mark.parametrize("name", ["unrelated-empty", "wheels/unrelated-empty"])
def test_replay_rejects_unlisted_empty_directories_and_preserves_them(case, name):
    prepare(case)
    extra = case["output"] / name
    extra.mkdir()
    with pytest.raises(ContainerError, match="unlisted directory"):
        prepare(case)
    assert extra.is_dir()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file fixture")
def test_replay_rejects_special_entries_without_opening_them(case):
    prepare(case)
    fifo = case["output"] / "unrelated-fifo"
    os.mkfifo(fifo)
    with pytest.raises(ContainerError, match="non-regular"):
        prepare(case)
    assert fifo.exists()


@pytest.mark.parametrize("base", [
    "python:latest", "https://registry.invalid/python@sha256:" + "a" * 64,
    "user:password@registry.invalid/python@sha256:" + "a" * 64,
    "python@sha256:" + "A" * 64, "python@sha256:" + "a" * 63, "python\nRUN unsafe",
    "approved.invalid/python:bad:tag@sha256:" + "a" * 64,
])
def test_base_pin_validation_before_writes(case, base):
    with pytest.raises(ContainerError):
        prepare(case, base_image=base)
    assert not case["output"].exists()


@pytest.mark.parametrize("field,value", [
    ("python_version", "3.12"), ("python_version", "3.12.0rc1"),
    ("python_version", None), ("platform", "windows/amd64"),
    ("glibc_version", "musl"), ("glibc_version", "2.1000000"),
])
def test_explicit_target_validation(case, field, value):
    with pytest.raises(ContainerError):
        prepare(case, target=replace(TARGET, **{field: value}))
    assert not case["output"].exists()


@pytest.mark.parametrize("changes", [{"uid": 0}, {"gid": True}, {"uid": -1}])
def test_nonroot_identity_required(case, changes):
    with pytest.raises(ContainerError):
        prepare(case, **changes)
    assert not case["output"].exists()


def test_wrong_revision_no_output(case):
    with pytest.raises(ContainerError):
        prepare(case, expected_source_commit="b" * 40)
    assert not case["output"].exists()


@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate", "hash"])
def test_exact_hashed_native_wheel_set(case, kind):
    third = case["third_party_wheels"]
    if kind == "missing":
        next(third.glob("httpx-*.whl")).unlink()
    elif kind == "extra":
        wheels._make_wheel(third, "unknown", "1.0")
    elif kind == "duplicate":
        wheels._make_wheel(third, "httpx", "0.29.0")
    else:
        with next(third.glob("httpx-*.whl")).open("ab") as stream:
            stream.write(b"changed")
    with pytest.raises(ContainerError):
        prepare(case)
    assert not case["output"].exists()


def test_missing_transitive_native_requirement(case):
    source = next(case["third_party_wheels"].glob("httpx-*.whl"))
    wheels._mutate_metadata(case["third_party_wheels"], lambda text: text.replace(
        "Version: 0.28.0", "Version: 0.28.0\nRequires-Dist: missing-package>=1",
    ), name="httpx")
    lock = case["third_party_lock"]
    original = lock.read_text()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    lines = original.splitlines()
    lines[0] = f"httpx==0.28.0 --hash=sha256:{digest}"
    lock.write_text("\n".join(lines))
    with pytest.raises(ContainerError, match="closure"):
        prepare(case)


def test_secret_url_requirement_rejected(case):
    case["third_party_lock"].write_text(
        "httpx @ https://user:password@invalid.example/pkg.whl --hash=sha256:" + "a" * 64,
    )
    with pytest.raises(ContainerError):
        prepare(case)
    assert not case["output"].exists()


def test_snapshot_tamper_detected_and_owned_output_removed(case, monkeypatch):
    original = container._copy

    def changed(source, target, digest):
        original(source, target, digest)
        with target.open("ab") as stream:
            stream.write(b"changed")

    monkeypatch.setattr(container, "_copy", changed)
    with pytest.raises(ContainerError):
        prepare(case)
    assert not case["output"].exists()


def test_output_overlap_or_symlink_rejected(case, tmp_path):
    with pytest.raises(ContainerError):
        prepare(case, output=case["third_party_wheels"] / "image")
    link = tmp_path / "linked-image"
    target = tmp_path / "elsewhere"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(str(exc))
    with pytest.raises(ContainerError):
        prepare(case, output=link)
    assert not list(target.iterdir())


def test_build_guard_checks_actual_bytes_before_any_install(case):
    result = prepare(case)
    output = case["output"]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    command = [sys.executable, "-I", "-B", str(output / "verify-and-install.py"),
               result["contract_sha256"], "--verify-only"]
    good = subprocess.run(command, capture_output=True, text=True, env=env,
                          timeout=15, **no_window_kwargs())
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout) == {"verified": True}
    (output / "install.txt").write_text("tampered")
    bad = subprocess.run(command, capture_output=True, text=True, env=env,
                         timeout=15, **no_window_kwargs())
    assert bad.returncode != 0
    assert "digest mismatch" in bad.stderr


def test_container_cli_preserves_normal_commands(case, capsys):
    assert main(["container", "prepare", "--descriptor", str(case["descriptor_path"]),
                 "--expected-source-commit", wheels.COMMIT,
                 "--third-party-lock", str(case["third_party_lock"]),
                 "--third-party-wheels", str(case["third_party_wheels"]),
                 "--output", str(case["output"]), "--base-image", BASE,
                 "--python-version", TARGET.python_version, "--platform", TARGET.platform,
                 "--glibc-version", TARGET.glibc_version]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "prepared"
    from agent_index_service.__main__ import build_parser

    assert build_parser().parse_args(["serve", "--config", "/tmp/host.yaml"]).command == "serve"


def test_conflicting_image_identity_keeps_existing_context(case):
    prepare(case)
    before = container._files(case["output"])
    with pytest.raises(ContainerError, match="conflicts"):
        prepare(case, uid=10002)
    assert container._files(case["output"]) == before


def test_model_stack_cannot_enter_service_image(case):
    case["third_party_lock"].write_text(
        "torch==1.0 --hash=sha256:" + "a" * 64,
    )
    with pytest.raises(ContainerError, match="engine/model"):
        prepare(case)
    assert not case["output"].exists()


def test_native_extra_transitive_requirement_cannot_be_omitted(case):
    wheels._mutate_metadata(case["descriptor_path"].parent, lambda text: text.replace(
        "Name: agent-index",
        'Name: agent-index\nRequires-Dist: native-only>=1; extra == "store"',
    ), name="agent-index")
    case["descriptor_path"].write_text(json.dumps(wheels.build_descriptor(
        case["descriptor_path"].parent, source_commit=wheels.COMMIT,
    )))
    with pytest.raises(ContainerError, match="native-only"):
        prepare(case)


def test_wrong_python_tag_cannot_enter_linux_context(case):
    path = next(case["third_party_wheels"].glob("httpx-*.whl"))
    wheels._mutate_metadata(case["third_party_wheels"],
                           lambda text: text.replace("py3-none-any", "cp311-none-win_amd64"),
                           name="httpx", record="WHEEL")
    target = path.with_name(path.name.replace("py3-none-any", "cp311-none-win_amd64"))
    path.rename(target)
    lock = case["third_party_lock"]
    lines = lock.read_text().splitlines()
    lines[0] = "httpx==0.28.0 --hash=sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    lock.write_text("\n".join(lines))
    with pytest.raises(ContainerError, match="tags"):
        prepare(case)


@pytest.mark.parametrize("size", [4096, 4097])
def test_bounded_native_members_without_widening_release_budget(case, monkeypatch, size):
    from agent_index_service import release

    assert release._MEMBER_LIMIT == 64 * 1024 * 1024
    assert release._NATIVE_MEMBER_LIMIT == 256 * 1024 * 1024
    assert release._TOTAL_LIMIT == 256 * 1024 * 1024
    monkeypatch.setattr(release, "_MEMBER_LIMIT", 1024)
    monkeypatch.setattr(release, "_NATIVE_MEMBER_LIMIT", 4096)
    path = next(case["third_party_wheels"].glob("httpx-*.whl"))

    def payload(entries):
        return {**entries, "httpx/native.so": b"x" * size}

    wheels._rewrite(path, payload)
    lines = case["third_party_lock"].read_text().splitlines()
    lines[0] = "httpx==0.28.0 --hash=sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    case["third_party_lock"].write_text("\n".join(lines))
    with pytest.raises(release.ReleaseError, match="member decompression"):
        release.inspect_wheel(path, distributions=frozenset({"httpx"}))
    if size == 4096:
        assert prepare(case)["state"] == "prepared"
        monkeypatch.setattr(release, "_TOTAL_LIMIT", 4096)
        with pytest.raises(release.ReleaseError, match="total decompression"):
            release.inspect_wheel(path, distributions=frozenset({"httpx"}), native=True)
    else:
        with pytest.raises(ContainerError, match="member decompression"):
            prepare(case)
        assert not case["output"].exists()


@pytest.mark.parametrize("platform,architecture,minimum", [
    ("linux/amd64", "x86_64", 5), ("linux/arm64", "aarch64", 17),
])
def test_manylinux_policy_floor_matches_architecture(platform, architecture, minimum):
    from packaging.tags import Tag

    tags = replace(TARGET, platform=platform).tags()
    assert Tag("py3", "none", f"manylinux_2_{minimum}_{architecture}") in tags
    assert Tag("py3", "none", f"manylinux_2_{minimum - 1}_{architecture}") not in tags


def test_unsupported_old_arm_policy_cannot_enter_context(case):
    path = next(case["third_party_wheels"].glob("httpx-*.whl"))
    wheels._mutate_metadata(case["third_party_wheels"],
                           lambda text: text.replace("py3-none-any", "py3-none-manylinux_2_16_aarch64"),
                           name="httpx", record="WHEEL")
    renamed = path.with_name(path.name.replace("py3-none-any", "py3-none-manylinux_2_16_aarch64"))
    path.rename(renamed)
    lines = case["third_party_lock"].read_text().splitlines()
    lines[0] = "httpx==0.28.0 --hash=sha256:" + hashlib.sha256(renamed.read_bytes()).hexdigest()
    case["third_party_lock"].write_text("\n".join(lines))
    with pytest.raises(ContainerError, match="tags"):
        prepare(case, target=replace(TARGET, platform="linux/arm64"))
    assert not case["output"].exists()


@pytest.mark.parametrize("name", ["Dockerfile", ".dockerignore"])
def test_recipe_tamper_rejected_before_any_docker_execution(case, monkeypatch, name, tmp_path):
    result = prepare(case)
    (case["output"] / name).write_text("tampered recipe or ignore")
    docker = tmp_path / "docker"
    docker.write_bytes(b"explicit fake executable")
    monkeypatch.setattr(container, "_run", lambda *a, **kw: pytest.fail("Docker must not start"))
    with pytest.raises(ContainerError, match="digest"):
        container.build_container_context(
            case["output"], expected_contract_sha256=result["contract_sha256"], docker=docker,
        )


def test_builder_uses_only_captured_bytes_after_verification(case, monkeypatch, tmp_path):
    result = prepare(case)
    expected = {str(path.relative_to(case["output"])).replace("\\", "/"): path.read_bytes()
                for path in case["output"].rglob("*") if path.is_file()}
    docker = tmp_path / "docker"
    docker.write_bytes(b"fake executable")
    commands = []

    def run(argv, *, cwd, env, deadline, input_stream=None, operation_label=None):
        commands.append(argv)
        assert "--context" in argv
        if "inspect" in argv:
            assert operation_label == "cached image inspection"
            # Mutation after snapshot verification must not reach the builder.
            (case["output"] / "Dockerfile").write_text("malicious replacement")
            (case["output"] / ".dockerignore").write_text("COPY host secrets")
            return '{"os":"linux","architecture":"amd64"}'
        assert argv[-1] == "-"
        assert operation_label == "container image build"
        assert "--network=none" in argv
        assert "CONTEXT_SHA256=" + result["contract_sha256"] in argv
        assert str(case["output"]) not in argv
        with tarfile.open(fileobj=input_stream, mode="r") as snapshot:
            actual = {item.name: snapshot.extractfile(item).read() for item in snapshot.getmembers()}
        assert actual == expected
        container.Path(argv[argv.index("--iidfile") + 1]).write_text("sha256:" + "b" * 64)
        return ""

    monkeypatch.setattr(container, "_run", run)
    built = container.build_container_context(
        case["output"], expected_contract_sha256=result["contract_sha256"], docker=docker,
    )
    assert built["state"] == "built"
    assert built["image_id"] == "sha256:" + "b" * 64
    assert len(commands) == 2


@pytest.mark.parametrize("digest", ["a" * 64, "A" * 64, "", "too short"])
def test_independent_context_pin_must_match_before_builder(case, monkeypatch, digest):
    prepare(case)
    monkeypatch.setattr(container, "_run", lambda *a, **kw: pytest.fail("Docker must not start"))
    with pytest.raises(ContainerError):
        container.build_container_context(case["output"], expected_contract_sha256=digest)


def test_inside_build_argument_digest_mismatch_is_explicit(case):
    prepare(case)
    script = case["output"] / "verify-and-install.py"
    command = [sys.executable, "-I", "-B", "-S", str(script), "--verify-only"]
    # BuildKit ARG is environment-visible for RUN, not persisted into the runtime stage.
    env = {"CONTEXT_SHA256": "0" * 64}
    result = subprocess.run(command, capture_output=True, text=True, env=env,
                            timeout=15, **no_window_kwargs())
    assert result.returncode != 0
    assert "context contract digest mismatch" in result.stderr


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), KeyboardInterrupt(), OSError("failed")])
def test_builder_failure_reports_daemon_uncertainty(case, monkeypatch, tmp_path, failure):
    prepared = prepare(case)
    docker = tmp_path / "docker"
    docker.write_bytes(b"fake executable")

    def run(argv, **kwargs):
        if "inspect" in argv:
            return '{"os":"linux","architecture":"amd64"}'
        raise failure

    monkeypatch.setattr(container, "_run", run)
    with pytest.raises(ContainerError, match="daemon outcome uncertain") as raised:
        container.build_container_context(
            case["output"], expected_contract_sha256=prepared["contract_sha256"], docker=docker,
        )
    assert raised.value.__cause__ is failure


def test_snapshot_limits_fail_before_builder(case, monkeypatch):
    result = prepare(case)
    monkeypatch.setattr(container, "_CONTEXT_LIMIT", 100)
    monkeypatch.setattr(container, "_run", lambda *a, **kw: pytest.fail("Docker must not start"))
    with pytest.raises(ContainerError, match="limit"):
        container.build_container_context(
            case["output"], expected_contract_sha256=result["contract_sha256"],
        )


def test_race_during_snapshot_capture_is_not_accepted(case, monkeypatch):
    result = prepare(case)
    actual_add = tarfile.TarFile.addfile

    def race(archive, info, fileobj=None):
        if info.name == "Dockerfile":
            with (case["output"] / "Dockerfile").open("ab") as output:
                output.write(b"changed")
        return actual_add(archive, info, fileobj)

    monkeypatch.setattr(tarfile.TarFile, "addfile", race)
    with pytest.raises(ContainerError, match="changed"):
        with container.verified_context_snapshot(
            case["output"], expected_contract_sha256=result["contract_sha256"],
        ):
            pytest.fail("unstable snapshot must not be yielded")


@pytest.mark.parametrize("cleanup", [OSError("cleanup IO"), ValueError("cleanup fence")])
def test_preparation_cleanup_error_keeps_primary_failure(case, monkeypatch, cleanup):
    primary = ValueError("primary snapshot mismatch")
    monkeypatch.setattr(container, "_copy", lambda *a, **kw: (_ for _ in ()).throw(primary))
    monkeypatch.setattr(container.shutil, "rmtree",
                        lambda *a, **kw: (_ for _ in ()).throw(cleanup))
    with pytest.raises(container.ContextCleanupError) as raised:
        prepare(case)
    assert raised.value.__cause__ is primary
    assert type(primary).__name__ in str(raised.value)
    assert type(cleanup).__name__ in str(raised.value)
    assert case["output"].exists()


def test_preparation_missing_output_does_not_mask_primary(case, monkeypatch):
    primary = ValueError("primary snapshot mismatch")
    real_remove = container.shutil.rmtree

    def fail(source, target, expected):
        real_remove(case["output"])
        raise primary

    monkeypatch.setattr(container, "_copy", fail)
    with pytest.raises(ContainerError) as raised:
        prepare(case)
    assert raised.value.__cause__ is primary


def test_unknown_preparation_ownership_never_deletes_output(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "unrelated").write_text("keep")
    primary = OSError("failed to capture ownership")
    with pytest.raises(container.ContextCleanupError) as raised:
        container._cleanup_preparation(output, None, primary)
    assert raised.value.__cause__ is primary
    assert (output / "unrelated").read_text() == "keep"


@pytest.mark.parametrize("suffix", [
    "", " --hash=", " --hash=sha256:", " --hash=md5:" + "a" * 32,
    " --hash=sha256:" + "a" * 63, " --hash=sha256:" + "A" * 64,
    " --hash=sha256:" + "a" * 64 + " --index-url=unapproved",
])
def test_existing_hash_lock_rejections_are_preserved(suffix):
    from agent_index_service.staging import CandidateError, hashed_requirements

    with pytest.raises(CandidateError, match="sha256"):
        hashed_requirements(("httpx==0.28.0" + suffix).encode())


def test_hash_lock_record_has_precise_typed_shape():
    from agent_index_service.staging import HashedRequirement, hashed_requirements

    assert set(HashedRequirement.__annotations__) == {"version", "extras", "sha256"}
    assert hashed_requirements(
        ("httpx[http2]==0.28.0 --hash=sha256:" + "a" * 64).encode(),
    ) == {"httpx": {"version": "0.28.0", "extras": ["http2"], "sha256": ["a" * 64]}}


def test_cleanup_symlink_swap_preserves_primary_and_other_tree(tmp_path):
    output, outside = tmp_path / "output", tmp_path / "outside"
    output.mkdir()
    identity = output.stat()
    output.rmdir()
    outside.mkdir()
    (outside / "keep").write_text("unrelated")
    try:
        output.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(str(exc))
    primary = ValueError("primary failure")
    with pytest.raises(container.ContextCleanupError) as raised:
        container._cleanup_preparation(output, identity, primary)
    assert raised.value.__cause__ is primary
    assert (outside / "keep").read_text() == "unrelated"


def test_valid_build_argument_and_wrong_pin_are_independent(case):
    prepared = prepare(case)
    script = case["output"] / "verify-and-install.py"
    command = [sys.executable, "-I", "-B", "-S", str(script), "--verify-only"]
    env = {**os.environ, "CONTEXT_SHA256": prepared["contract_sha256"]}
    result = subprocess.run(command, capture_output=True, text=True, env=env,
                            timeout=15, **no_window_kwargs())
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"verified": True}


def test_snapshot_metadata_and_bytes_ignore_input_timestamps(case):
    import io

    prepared = prepare(case)
    with container.verified_context_snapshot(
        case["output"], expected_contract_sha256=prepared["contract_sha256"],
    ) as (_contract, snapshot):
        first = snapshot.read()
    for path in case["output"].rglob("*"):
        if path.is_file():
            os.utime(path, (1700000000, 1700000000))
    with container.verified_context_snapshot(
        case["output"], expected_contract_sha256=prepared["contract_sha256"],
    ) as (_contract, snapshot):
        assert snapshot.read() == first
    with tarfile.open(fileobj=io.BytesIO(first)) as archive:
        for entry in archive.getmembers():
            assert (entry.mtime, entry.uid, entry.gid, entry.uname, entry.gname) == (0, 0, 0, "", "")


def test_unrelated_build_tokens_do_not_infer_docker_operations():
    from agent_index_service.staging import _operation

    assert _operation(["python", "-c", "pass", "build"]) == "isolated runtime probe"
    assert _operation(["custom-tool", "image", "inspect"]) == "build operation"
