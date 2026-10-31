"""Deliver canonical construction resources without a runtime checkout dependency."""

import os
from pathlib import Path
import runpy
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
    name: canonical.parent / "installer-engine" / name if canonical else None
    for name in (
        "installer_engine.py", "installer_engine_process.py",
        "installer_dependency_policy.py", "installer_dependency_config.py",
        "installer_wheel_inputs.py", "installer_wheel_snapshot.py", "runtime_launcher.py",
    )
}
setup(cmdclass=helpers["commands"](
    project, "agent_chat_service", canonical, companion_resources=companions,
))
