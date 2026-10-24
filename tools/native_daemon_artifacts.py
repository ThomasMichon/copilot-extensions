"""Source-bound native daemon assets for explicit artifact construction only."""

from __future__ import annotations

import hashlib
from pathlib import Path
import platform
import stat
import tempfile
from types import ModuleType
import sys


TRUSTED_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path("src") / "agent_procutil"
SOURCE = "daemon_instance_native.c"
BUILD = "_build_native.py"
MACHINES = {"x86_64": 62, "aarch64": 183}


def _fail(message: str):
    from artifact_source_build import PromotionArtifactError

    raise PromotionArtifactError(message)


def _ordinary(path: Path, root: Path) -> bytes:
    if not path.resolve().is_relative_to(root.resolve()):
        _fail("native daemon build input escapes its source")
    for entry in (path, *path.parents):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("native daemon build input is redirected")
        if entry == root:
            break
    if not path.is_file():
        _fail("native daemon build input is not an ordinary file")
    return path.read_bytes()


def trusted_setup(source_dir: Path, data: dict) -> bytes | None:
    """Admit only the registered procutil hook, not arbitrary executable setup."""
    project = data.get("project")
    name = project.get("name", "") if isinstance(project, dict) else ""
    if not isinstance(name, str):
        return None
    name = name.replace("_", "-").lower()
    if name != "agent-procutil":
        return None
    intent = any(
        (source_dir / PACKAGE / name).exists()
        for name in (SOURCE, BUILD, "daemon_instances.sh", "native")
    )
    if not intent and not (source_dir / "setup.py").exists():
        return None
    canonical = TRUSTED_ROOT / "libs" / "agent-procutil"
    try:
        for relative in (Path("setup.py"), PACKAGE / BUILD):
            if _ordinary(source_dir / relative, source_dir) != _ordinary(canonical / relative, canonical):
                _fail("native daemon build hook differs from the trusted construction owner")
        _ordinary(source_dir / PACKAGE / SOURCE, source_dir)
    except OSError as error:
        _fail(f"native daemon construction ownership is incomplete: {type(error).__name__}")
    return (canonical / "setup.py").read_bytes()


def _compile_expected(source_dir: Path, arch: str) -> bytes:
    if not sys.platform.startswith("linux") or platform.machine() != arch:
        _fail("native daemon artifact verification requires its explicit Linux build platform")
    scratch = Path.cwd() / ".test-runs"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-artifact-verify-", dir=scratch) as directory:
        root = Path(directory)
        helper = root / BUILD
        helper.write_bytes(_ordinary(source_dir / PACKAGE / BUILD, source_dir))
        (root / SOURCE).write_bytes(_ordinary(source_dir / PACKAGE / SOURCE, source_dir))
        owner = ModuleType("_trusted_native_daemon_build")
        owner.__file__ = str(helper)
        exec(compile(helper.read_bytes(), str(helper), "exec"), owner.__dict__)
        output = owner.build_native(root / "native" / f"linux-{arch}" / "daemon-instance")
        return output.read_bytes()


def generated_members(bundle, *, source_dir: Path, data: dict, prefix: str) -> dict[str, bytes]:
    """Rebuild the one declared ELF; never accept a self-asserted binary digest."""
    if trusted_setup(source_dir, data) is None:
        return {}
    wheel_headers = bundle.read(prefix + "WHEEL").decode("utf-8").splitlines()
    tags = [
        line.removeprefix("Tag: ").strip()
        for line in wheel_headers
        if line.startswith("Tag: ")
    ]
    platforms = {tag.rsplit("-", 1)[-1] for tag in tags}
    native = {name for name in bundle.namelist() if name.startswith("agent_procutil/native/")}
    supported = {f"linux_{arch}": arch for arch in MACHINES}
    selected = platforms & supported.keys()
    if not selected:
        if native or any("linux" in target for target in platforms):
            _fail("unsupported native daemon artifact target")
        return {}
    if len(platforms) != 1 or len(selected) != 1:
        _fail("ambiguous native daemon artifact target")
    if "Root-Is-Purelib: false" not in wheel_headers:
        _fail("native daemon artifact must retain its platform wheel installation mode")
    arch = supported[next(iter(selected))]
    member = f"agent_procutil/native/linux-{arch}/daemon-instance"
    if native != {member}:
        _fail("native daemon executable missing or unexpected native assets present")
    info = bundle.getinfo(member)
    mode = info.external_attr >> 16
    if stat.S_IFMT(mode) != stat.S_IFREG or not mode & 0o111:
        _fail("native daemon artifact is not an ordinary executable")
    actual = bundle.read(member)
    if (
        len(actual) < 64 or actual[:6] != b"\x7fELF\x02\x01"
        or int.from_bytes(actual[18:20], "little") != MACHINES[arch]
    ):
        _fail("native daemon executable has the wrong ELF platform")
    expected = _compile_expected(source_dir, arch)
    if hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
        _fail("native daemon executable differs from its source-bound explicit build")
    return {member: expected}


def wheel_assets(wheel: Path, *, source_dir: Path) -> dict[Path, bytes]:
    """Export only a source-verified built asset during explicit payload assembly."""
    import zipfile

    from build_python_artifacts import read_wheel_generator
    from promotion_python_artifacts import validate_wheel

    with wheel.open("rb") as stream:
        validate_wheel(
            stream, {"filename": wheel.name, "generator": read_wheel_generator(wheel)},
            source_dir=source_dir,
        )
    with zipfile.ZipFile(wheel) as bundle:
        members = [
            name for name in bundle.namelist()
            if name.startswith("agent_procutil/native/") and name.endswith("/daemon-instance")
        ]
        if len(members) != 1:
            _fail("native payload assembly requires one supported built executable")
        return {
            Path(members[0].removeprefix("agent_procutil/")): bundle.read(members[0]),
        }
