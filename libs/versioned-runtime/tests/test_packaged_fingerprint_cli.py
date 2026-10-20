"""Actual isolated runtime CLIs work in both shipped primitive layouts."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime as runtime

HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@pytest.fixture(params=["named", "packaged"])
def primitive(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    layout = tmp_path / "resources"
    layout.mkdir()
    builder = ast.parse((LIBRARY / "standalone_build.py").read_text(encoding="utf-8"))
    declaration = next(
        statement for statement in builder.body
        if isinstance(statement, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "RUNTIME_RESOURCES"
                for target in statement.targets)
    )
    resources = ast.literal_eval(declaration.value)
    for source, destination in resources.items():
        name = destination if request.param == "packaged" else source
        shutil.copyfile(LIBRARY / source, layout / name)
    entry = layout / ("_versioned_runtime.py" if request.param == "packaged" else "versioned_runtime.py")
    if request.param == "packaged":
        assert not (layout / "versioned_runtime.py").exists()
    return entry


def invoke(primitive: Path, root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", str(primitive), "--root", str(root), *arguments],
        cwd=primitive.parent, capture_output=True, text=True, timeout=10, creationflags=HEADLESS,
    )


@pytest.mark.parametrize("json_output", [False, True])
def test_fingerprint_cli_from_shipped_layout(
    primitive: Path, tmp_path: Path, json_output: bool,
) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "module.py").write_text("VALUE = 7\n", encoding="utf-8")
    expected = runtime.fingerprint_source([source])
    arguments = ["--json"] if json_output else []
    result = invoke(primitive, tmp_path / "runtime", [*arguments, "fingerprint", str(source)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    if json_output:
        assert json.loads(result.stdout) == {"fingerprint": expected}
    else:
        assert result.stdout == expected + "\n"


def test_fingerprint_cli_preserves_missing_input_refusal(primitive: Path, tmp_path: Path) -> None:
    missing = tmp_path / "missing-source"
    result = invoke(primitive, tmp_path / "runtime", ["fingerprint", str(missing)])
    assert result.returncode == 1
    assert result.stdout == ""
    assert "declared input path does not exist" in result.stderr
    assert str(missing) in result.stderr


@pytest.mark.parametrize("json_output", [False, True])
def test_redirect_cli_from_shipped_layout(
    primitive: Path, tmp_path: Path, json_output: bool,
) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    root = tmp_path / "runtime"
    arguments = ["--json"] if json_output else []
    values = {"payload-dir": str(payload.resolve()), "stamped-version": "1.2.3"}
    result = invoke(primitive, root, [
        *arguments, "publish-redirects", "--payload-dir", str(payload), "--version", "1.2.3",
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert {name: (root / name).read_text() for name in values} == {
        name: value + "\n" for name, value in values.items()
    }
    if json_output:
        assert json.loads(result.stdout) == {"published": values}
    else:
        assert result.stdout == ""


def publication_case(
    primitive: Path, tmp_path: Path, names: tuple[str, ...],
) -> tuple[Path, Path, Path, list[Path]]:
    root, evidence, payload = tmp_path / "runtime", tmp_path / "evidence", tmp_path / "payload"
    root.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    payload.mkdir()
    (payload / "module.py").write_text("VALUE = 7\n", encoding="utf-8")
    context = evidence / "context.json"
    arguments = ["publication-context", "--output", str(context)]
    targets = [root / name for name in names]
    for index, target in enumerate(targets):
        data = f"previous-{target.name}\n".encode()
        target.write_bytes(data)
        target.chmod(0o600)
        backup = evidence / f"before.{index}"
        backup.write_bytes(data)
        backup.chmod(0o600)
        arguments.extend(["--binding", str(target), str(backup), hashlib.sha256(data).hexdigest()])
    result = invoke(primitive, root, arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""
    return root, payload, context, targets


def proof_arguments(context: Path, receipts: list[Path], values: dict[Path, bytes]) -> list[str]:
    arguments = ["--context", str(context)]
    for receipt in receipts:
        arguments.extend(["--receipt", str(receipt)])
    for target, data in values.items():
        arguments.extend(["--expected", str(target), hashlib.sha256(data).hexdigest()])
    return arguments


def test_receipted_redirect_cli_check_restore_and_cleanup(primitive: Path, tmp_path: Path) -> None:
    root, payload, context, targets = publication_case(
        primitive, tmp_path, ("payload-dir", "stamped-version"),
    )
    before = {target: target.read_bytes() for target in targets}
    receipt, recovery = context.parent / "producer.jsonl", context.parent / "recovery.jsonl"
    result = invoke(primitive, root, [
        "publish-redirects", "--payload-dir", str(payload), "--version", "1.2.3",
        "--ownership-receipt", str(receipt),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""
    values = {targets[0]: (str(payload.resolve()) + "\n").encode(), targets[1]: b"1.2.3\n"}
    arguments = proof_arguments(context, [receipt], values)
    paths = [argument for target in targets for argument in ("--path", str(target))]
    result = invoke(primitive, root, ["publication-check", *arguments, *paths])
    assert result.returncode == 0, result.stdout + result.stderr
    result = invoke(primitive, root, [
        "publication-restore", *arguments, *paths, "--output-receipt", str(recovery),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""
    assert {target: target.read_bytes() for target in targets} == before
    arguments = proof_arguments(context, [receipt, recovery], values)
    result = invoke(primitive, root, ["publication-check", *arguments, *paths])
    assert result.returncode == 0, result.stdout + result.stderr
    result = invoke(primitive, root, ["publication-cleanup", *arguments])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""
    assert not any(path.exists() for path in (context, receipt, recovery))


@pytest.mark.parametrize("fault", ["corrupt", "same-bytes-peer"])
def test_receipt_cli_refuses_unowned_state(
    primitive: Path, tmp_path: Path, fault: str,
) -> None:
    root, payload, context, targets = publication_case(
        primitive, tmp_path, ("payload-dir", "stamped-version"),
    )
    receipt = context.parent / "producer.jsonl"
    result = invoke(primitive, root, [
        "publish-redirects", "--payload-dir", str(payload), "--version", "1.2.3",
        "--ownership-receipt", str(receipt),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    values = {target: target.read_bytes() for target in targets}
    if fault == "corrupt":
        receipt.write_bytes(receipt.read_bytes()[:-1])
    else:
        original = targets[0].stat()
        peer = root / "peer"
        peer.write_bytes(values[targets[0]])
        os.replace(peer, targets[0])
        assert targets[0].stat().st_ino != original.st_ino
    identities = {target: target.stat().st_ino for target in targets}
    arguments = proof_arguments(context, [receipt], values)
    paths = [argument for target in targets for argument in ("--path", str(target))]
    result = invoke(primitive, root, ["publication-check", *arguments, *paths])
    assert result.returncode == 1 and result.stdout == ""
    assert "incomplete" in result.stderr if fault == "corrupt" else "producer-created" in result.stderr
    result = invoke(primitive, root, [
        "publication-restore", *arguments, *paths,
        "--output-receipt", str(context.parent / "refused.jsonl"),
    ])
    assert result.returncode == 1 and result.stdout == ""
    assert {target: target.read_bytes() for target in targets} == values
    assert {target: target.stat().st_ino for target in targets} == identities
    assert context.exists() and receipt.exists()


def test_redirect_cli_partial_error_receipt_can_recover(primitive: Path, tmp_path: Path) -> None:
    root, payload, context, targets = publication_case(
        primitive, tmp_path, ("payload-dir", "stamped-version"),
    )
    before = {target: target.read_bytes() for target in targets}
    receipt, recovery = context.parent / "failed.jsonl", context.parent / "recovery.jsonl"
    bootstrap = """
import os, runpy, sys
from pathlib import Path
replace = os.replace
def fail_second(source, target):
    if Path(target).name == "stamped-version":
        raise OSError("injected second redirect failure")
    return replace(source, target)
os.replace = fail_second
entry = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(entry, run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", bootstrap, str(primitive), "--root", str(root),
         "publish-redirects", "--payload-dir", str(payload), "--version", "1.2.3",
         "--ownership-receipt", str(receipt)],
        cwd=primitive.parent, capture_output=True, text=True, timeout=10, creationflags=HEADLESS,
    )
    assert result.returncode == 1 and "injected second redirect failure" in result.stderr
    assert {target: target.read_bytes() for target in targets} == before
    rows = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert rows[-1]["outcome"] == "error"
    assert any(row.get("phase") == "recovery" for row in rows)
    values = {targets[0]: (str(payload.resolve()) + "\n").encode(), targets[1]: b"1.2.3\n"}
    arguments = proof_arguments(context, [receipt], values)
    result = invoke(primitive, root, [
        "publication-check", *arguments, "--path", str(targets[0]),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    result = invoke(primitive, root, [
        "publication-restore", *arguments, "--path", str(targets[0]),
        "--output-receipt", str(recovery),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    assert {target: target.read_bytes() for target in targets} == before
    result = invoke(primitive, root, [
        "publication-cleanup", *proof_arguments(context, [receipt, recovery], values),
    ])
    assert result.returncode == 0, result.stdout + result.stderr


def test_activation_receipt_cli_from_shipped_layout(primitive: Path, tmp_path: Path) -> None:
    root, _payload, context, targets = publication_case(
        primitive, tmp_path, ("current-version", "last-known-good"),
    )
    (root / "versions" / "1.2.3").mkdir(parents=True)
    before = {target: target.read_bytes() for target in targets}
    receipt, recovery = context.parent / "activate.jsonl", context.parent / "recovery.jsonl"
    result = invoke(primitive, root, [
        "--json", "activate", "1.2.3", "--no-link", "--ownership-receipt", str(receipt),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["activated"] == "1.2.3"
    values = {target: b"1.2.3\n" for target in targets}
    assert {target: target.read_bytes() for target in targets} == values
    arguments = proof_arguments(context, [receipt], values)
    paths = [argument for target in targets for argument in ("--path", str(target))]
    result = invoke(primitive, root, ["publication-check", *arguments, *paths])
    assert result.returncode == 0, result.stdout + result.stderr
    result = invoke(primitive, root, [
        "publication-restore", *arguments, *paths, "--output-receipt", str(recovery),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    assert {target: target.read_bytes() for target in targets} == before


@pytest.mark.parametrize("alias_kind", ["ordinary", "directory"])
def test_source_loader_refuses_foreign_primitive_alias(
    primitive: Path, tmp_path: Path, alias_kind: str,
) -> None:
    alias = primitive.with_name(
        "versioned_runtime.py" if primitive.name.startswith("_") else "_versioned_runtime.py",
    )
    if alias_kind == "ordinary":
        alias.write_text("raise AssertionError('foreign primitive executed')\n", encoding="utf-8")
    else:
        alias.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    root = tmp_path / "runtime"
    result = invoke(primitive, root, ["publish-redirects", "--payload-dir", str(payload)])
    assert result.returncode == 1 and result.stdout == ""
    assert "exactly one" in result.stderr if alias_kind == "ordinary" else "ordinary" in result.stderr
    assert "foreign primitive executed" not in result.stderr
    assert not (root / "payload-dir").exists()


def source_payload(payload: Path) -> tuple[str, str]:
    source = runtime._runtime_component("versioned_runtime_source")
    (payload / "plugin.json").write_text(
        json.dumps({"name": "example-plugin", "version": "1.2.3"}), encoding="utf-8",
    )
    commit, tree = "a" * 40, "b" * 40
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        "example-plugin", "1.2.3", commit, tree, digest,
        source.runtime_version("1.2.3", commit, digest),
    )
    (payload / source.SOURCE_FILE).write_text(json.dumps(identity.document()), encoding="utf-8")
    return identity.runtime_version, json.dumps(source.manifest_fields(identity), sort_keys=True)


@pytest.mark.parametrize("fault", ["none", "changed-source", "missing-pin"])
def test_source_redirect_cli_keeps_full_pin_validation(
    primitive: Path, tmp_path: Path, fault: str,
) -> None:
    root, payload, context, targets = publication_case(
        primitive, tmp_path, ("payload-dir", "stamped-version"),
    )
    version, pin = source_payload(payload)
    before = {target: target.read_bytes() for target in targets}
    receipt = context.parent / "source.jsonl"
    arguments = ["publish-redirects", "--payload-dir", str(payload), "--version", version,
                 "--ownership-receipt", str(receipt)]
    if fault != "missing-pin":
        arguments.extend(["--expected-source-identity", pin])
    if fault == "changed-source":
        (payload / "module.py").write_text("VALUE = 8\n", encoding="utf-8")
    result = invoke(primitive, root, arguments)
    if fault == "none":
        assert result.returncode == 0, result.stdout + result.stderr
        assert targets[0].read_text() == str(payload.resolve()) + "\n"
        assert targets[1].read_text() == version + "\n"
    else:
        assert result.returncode == 1 and result.stdout == ""
        assert "source" in result.stderr.lower()
        assert {target: target.read_bytes() for target in targets} == before
        assert json.loads(receipt.read_text().splitlines()[-1])["outcome"] == "error"


def test_source_snapshot_cli_uses_shipped_primitive(primitive: Path, tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    version, pin = source_payload(payload)
    root = tmp_path / "runtime"
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(primitive.with_name("versioned_runtime_source.py")),
         "--payload", str(payload), "--package-version", "1.2.3",
         "--expected-runtime-version", version, "--expected-source-identity", pin,
         "--snapshot-root", str(root)],
        cwd=primitive.parent, capture_output=True, text=True, timeout=10, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = root / "snapshots" / version
    assert Path(result.stdout.strip()) == snapshot
    assert (snapshot / "runtime-source.json").read_bytes() == (payload / "runtime-source.json").read_bytes()
