"""Deliver canonical runtime resources in independently buildable distributions."""

from pathlib import Path
import stat

from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

HELPER_NAME = "_standalone_runtime_build.py"
RUNTIME_RESOURCES = {
    "versioned_runtime.py": "_versioned_runtime.py",
    "versioned_runtime_leases.py": "versioned_runtime_leases.py",
    "versioned_runtime_construction.py": "versioned_runtime_construction.py",
    "versioned_runtime_publication.py": "versioned_runtime_publication.py",
    "versioned_runtime_source.py": "versioned_runtime_source.py",
}


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


def commands(project, package, canonical_directory=None, *, companion_resources=None):
    """Bind one package's hooks; generated sdist resources are not source mirrors."""
    project = Path(project).absolute()
    resources = {
        name: Path("src") / package / destination
        for name, destination in RUNTIME_RESOURCES.items()
    }
    canonical_directory = Path(canonical_directory) if canonical_directory else None

    def inputs():
        helper_paths = [project / HELPER_NAME]
        optional_helpers = []
        if canonical_directory is not None:
            optional_helpers = helper_paths
            helper_paths = [canonical_directory / "standalone_build.py"]
        primitives = {}
        for name, resource in resources.items():
            paths = [project / resource]
            optional = []
            if canonical_directory is not None:
                optional = paths
                paths = [canonical_directory / name]
            label = "runtime primitive" if name == "versioned_runtime.py" else name
            primitives[resource.name] = select_bytes(paths, label, optional_paths=optional)
        for name, source in (companion_resources or {}).items():
            included = project / "src" / package / name
            paths = [Path(source)] if source is not None else [included]
            primitives[name] = select_bytes(
                paths, name, optional_paths=[included] if source is not None else [],
            )
        return (
            primitives,
            select_bytes(helper_paths, "runtime build helper", optional_paths=optional_helpers),
        )

    class BuildPy(build_py):
        def run(self):
            primitives, _ = inputs()
            super().run()
            for name, data in primitives.items():
                destination = Path(self.build_lib) / package / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)

        def get_outputs(self, include_bytecode=1):
            outputs = super().get_outputs(include_bytecode)
            for filename in (*[resource.name for resource in resources.values()],
                             *(companion_resources or {})):
                name = str(Path(self.build_lib) / package / filename)
                if name not in outputs:
                    outputs.append(name)
            return outputs

    class BuildSdist(sdist):
        def make_release_tree(self, base_dir, files):
            primitives, helper = inputs()
            super().make_release_tree(base_dir, files)
            for name, data in primitives.items():
                destination = Path(base_dir) / "src" / package / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            (Path(base_dir) / HELPER_NAME).write_bytes(helper)

    return {"build_py": BuildPy, "sdist": BuildSdist}
