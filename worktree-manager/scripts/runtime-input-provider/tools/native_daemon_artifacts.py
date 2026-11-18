"""Admit the pure-Python process owner and reject legacy native wheel assets."""

from __future__ import annotations

from pathlib import Path
import stat


TRUSTED_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path("src") / "agent_procutil"


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
    """Admit only the canonical inert setuptools shim, never a native builder."""
    project = data.get("project")
    name = project.get("name", "") if isinstance(project, dict) else ""
    if not isinstance(name, str):
        return None
    name = name.replace("_", "-").lower()
    if name != "agent-procutil":
        return None
    setup = source_dir / "setup.py"
    if not setup.exists() and not setup.is_symlink():
        return None
    canonical = TRUSTED_ROOT / "libs" / "agent-procutil"
    try:
        if _ordinary(setup, source_dir) != _ordinary(canonical / "setup.py", canonical):
            _fail("process owner setup differs from the trusted construction owner")
    except OSError as error:
        _fail(f"process owner construction ownership is incomplete: {type(error).__name__}")
    return (canonical / "setup.py").read_bytes()


def generated_members(bundle, *, source_dir: Path, data: dict, prefix: str) -> dict[str, bytes]:
    """Legacy compiled assets never receive generated-member authority."""
    project = data.get("project", {})
    if not isinstance(project, dict) or not isinstance(project.get("name"), str):
        return {}
    if project["name"].replace("_", "-").lower() != "agent-procutil":
        return {}
    wheel_headers = bundle.read(prefix + "WHEEL").decode("utf-8").splitlines()
    tags = [
        line.removeprefix("Tag: ").strip()
        for line in wheel_headers
        if line.startswith("Tag: ")
    ]
    platforms = {tag.rsplit("-", 1)[-1] for tag in tags}
    if (
        platforms != {"any"} or "Root-Is-Purelib: true" not in wheel_headers
        or any(name.startswith("agent_procutil/native/") for name in bundle.namelist())
    ):
        _fail("process owner must be a pure Python wheel without native assets")
    return {}
