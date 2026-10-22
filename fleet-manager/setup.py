"""Artifact-contained lifecycle resources; no plugin or checkout at runtime."""

import runpy
import os
from pathlib import Path
import stat

from setuptools import setup

project = Path(__file__).absolute().parent
included = project / "_standalone_runtime_build.py"
directories = [
    parent / "libs" / "versioned-runtime" for parent in list(project.parents)[:3]
    if os.path.lexists(parent / "libs" / "versioned-runtime")
]
if len(directories) > 1:
    raise RuntimeError("ambiguous canonical runtime resource build helper")
canonical = directories[0] if directories else None
selected = canonical / "standalone_build.py" if canonical else included
for path in (*selected.parents, selected):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise RuntimeError("runtime build helper must not be linked")
if not selected.is_file():
    raise RuntimeError("runtime build helper must be a regular file")
helpers = runpy.run_path(str(selected))
helpers["select_bytes"](
    [selected], "runtime build helper", optional_paths=[included] if canonical else [],
)
companions = {
    "_runtime_launcher.py": (
        canonical.parent / "installer-engine" / "runtime_launcher.py" if canonical else None
    ),
    "_installer_process.py": (
        canonical.parent / "installer-engine" / "installer_engine_process.py" if canonical else None
    ),
    "_construction_procutil.py": (
        canonical.parent / "agent-procutil" / "src" / "agent_procutil" / "__init__.py" if canonical else None
    ),
}
setup(cmdclass=helpers["commands"](
    project, "fleet_manager", canonical, companion_resources=companions,
))
