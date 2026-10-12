"""Ship the unchanged canonical slot primitive in wheel/sdist build artifacts."""

from __future__ import annotations

from pathlib import Path

from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

PROJECT = Path(__file__).resolve().parent
RESOURCE = Path("src") / "agent_index_service" / "_versioned_runtime.py"


def resource_bytes(project: Path = PROJECT) -> bytes:
    canonical = project.parent / "libs" / "versioned-runtime" / "versioned_runtime.py"
    included = project / RESOURCE
    candidates = []
    for path in (canonical, included):
        if path.is_symlink():
            raise RuntimeError("runtime primitive build input must not be a symlink")
        if path.is_file():
            candidates.append(path.read_bytes())
    if not candidates:
        raise RuntimeError(
            "runtime primitive is missing; build from the full release checkout or its complete sdist"
        )
    if any(data != candidates[0] for data in candidates[1:]):
        raise RuntimeError("included runtime primitive differs from the canonical source")
    return candidates[0]


class BuildPy(build_py):
    def run(self) -> None:
        data = resource_bytes()
        super().run()
        destination = Path(self.build_lib) / "agent_index_service" / RESOURCE.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def get_outputs(self, include_bytecode: int = 1) -> list[str]:
        outputs = super().get_outputs(include_bytecode)
        resource = str(Path(self.build_lib) / "agent_index_service" / RESOURCE.name)
        if resource not in outputs:
            outputs.append(resource)
        return outputs


class BuildSdist(sdist):
    def make_release_tree(self, base_dir: str, files: list[str]) -> None:
        data = resource_bytes()
        super().make_release_tree(base_dir, files)
        destination = Path(base_dir) / RESOURCE
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
