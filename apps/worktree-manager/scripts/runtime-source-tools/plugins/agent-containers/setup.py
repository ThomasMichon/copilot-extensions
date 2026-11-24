"""Package the synchronized runtime resources, not an installer-path import."""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import stat

from setuptools import setup

project = Path(__file__).absolute().parent
scripts = project / "scripts"
canonical = scripts if os.path.lexists(scripts) else None
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
    project, "agent_containers", canonical,
    companion_resources={
        "_runtime_profile.py": scripts / "runtime_profile.py" if canonical else None,
    },
))
