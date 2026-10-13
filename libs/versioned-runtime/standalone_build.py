"""Deliver canonical runtime resources in independently buildable distributions."""

from pathlib import Path
import stat

from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

HELPER_NAME = "_standalone_runtime_build.py"


def ordinary_file(path):
    """Reject linked build inputs, including linked parent directories."""
    for entry in (*reversed(path.parents), path):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise RuntimeError("runtime resource build input must not be linked")
        if entry != path and not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("runtime resource build input ancestor must be a directory")
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("runtime resource build input must be a regular file")
    return True


def select_bytes(paths, label, *, optional_paths=()):
    candidates = []
    for path in paths:
        if not ordinary_file(path):
            raise RuntimeError(
                f"{label} is missing; build from the full release checkout or its complete sdist"
            )
        candidates.append(path.read_bytes())
    if not candidates:
        raise RuntimeError(
            f"{label} is missing; build from the full release checkout or its complete sdist"
        )
    candidates.extend(path.read_bytes() for path in optional_paths if ordinary_file(path))
    if any(data != candidates[0] for data in candidates[1:]):
        raise RuntimeError(f"included {label} differs from the canonical source")
    return candidates[0]


def commands(project, package, canonical_directory=None):
    """Bind one package's hooks; generated sdist resources are not source mirrors."""
    project = Path(project).absolute()
    resource = Path("src") / package / "_versioned_runtime.py"
    canonical_directory = Path(canonical_directory) if canonical_directory else None

    def inputs():
        primitive_paths = [project / resource]
        helper_paths = [project / HELPER_NAME]
        optional_primitives = []
        optional_helpers = []
        if canonical_directory is not None:
            optional_primitives = primitive_paths
            optional_helpers = helper_paths
            primitive_paths = [canonical_directory / "versioned_runtime.py"]
            helper_paths = [canonical_directory / "standalone_build.py"]
        return (
            select_bytes(primitive_paths, "runtime primitive", optional_paths=optional_primitives),
            select_bytes(helper_paths, "runtime build helper", optional_paths=optional_helpers),
        )

    class BuildPy(build_py):
        def run(self):
            data, _ = inputs()
            super().run()
            destination = Path(self.build_lib) / package / resource.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)

        def get_outputs(self, include_bytecode=1):
            outputs = super().get_outputs(include_bytecode)
            name = str(Path(self.build_lib) / package / resource.name)
            if name not in outputs:
                outputs.append(name)
            return outputs

    class BuildSdist(sdist):
        def make_release_tree(self, base_dir, files):
            data, helper = inputs()
            super().make_release_tree(base_dir, files)
            destination = Path(base_dir) / resource
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            (Path(base_dir) / HELPER_NAME).write_bytes(helper)

    return {"build_py": BuildPy, "sdist": BuildSdist}
