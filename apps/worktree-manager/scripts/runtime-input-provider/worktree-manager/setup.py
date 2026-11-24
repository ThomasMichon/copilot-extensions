"""Package operational assets using the canonical resource build owner."""
import os
from pathlib import Path
import runpy
import stat

from setuptools import setup

project = Path(__file__).absolute().parent
directories = [
    parent / "libs" / "versioned-runtime" for parent in list(project.parents)[:3]
    if os.path.lexists(parent / "libs" / "versioned-runtime")
]
if len(directories) > 1:
    raise RuntimeError("ambiguous canonical runtime resource build helper")
canonical = directories[0] if directories else None
if canonical is None and (project / "scripts" / "standalone_build.py").is_file():
    canonical = project / "scripts"
included = project / "_standalone_runtime_build.py"
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
setup(cmdclass=helpers["commands"](
    project, "worktree_manager", canonical,
    companion_resources=helpers["application_resources"](project, "worktree_manager"),
))
