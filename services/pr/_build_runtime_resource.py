"""Deliver canonical attribution helpers without an attribution runtime dependency."""

import os
from pathlib import Path
import runpy
import stat

PROJECT = Path(__file__).absolute().parent
included = PROJECT / "_standalone_runtime_build.py"
directories = [
    parent / "libs" / "versioned-runtime" for parent in list(PROJECT.parents)[:3]
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
attribution = canonical.parents[1] / "plugins" / "ai-attribution" / "scripts" if canonical else None
hooks = helpers["commands"](
    PROJECT, "agent_pull_requests_service", canonical, runtime_resources={},
    companion_resources={
        "_attribution/" + name: attribution / name if attribution else None
        for name in ("attribution_profile.py", "comment_preparation.py")
    },
)
BuildPy, BuildSdist = hooks["build_py"], hooks["sdist"]
