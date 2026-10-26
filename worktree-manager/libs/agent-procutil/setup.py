"""Build the supported Linux kernel binding into its platform wheel."""

from pathlib import Path
import platform
import runpy
import sys

from setuptools import Distribution, setup
from setuptools.command.build_py import build_py
from setuptools.command.bdist_wheel import bdist_wheel


SUPPORTED = sys.platform.startswith("linux") and platform.machine() in ("x86_64", "aarch64")


class BuildPy(build_py):
    def find_data_files(self, package, src_dir):
        # A source-tree native build is not a release artifact for this target.
        return [
            filename for filename in super().find_data_files(package, src_dir)
            if not (
                Path(filename).name == "daemon-instance"
                and "native" in Path(filename).parts
            )
        ]

    def run(self):
        super().run()
        package = Path(self.build_lib).resolve() / "agent_procutil"
        source = (Path(__file__).parent / "src" / "agent_procutil").resolve()
        native = package / "native"
        if package.is_relative_to(source):
            raise RuntimeError("native package output must not be the source tree")
        if native.exists():
            if native.is_symlink() or not native.resolve().is_relative_to(package):
                raise RuntimeError("native package output is redirected")
            for artifact in native.rglob("daemon-instance"):
                if not artifact.is_file() and not artifact.is_symlink():
                    raise RuntimeError("invalid native package build output")
                artifact.unlink()
        if SUPPORTED:
            build = runpy.run_path(
                str(Path(__file__).parent / "src" / "agent_procutil" / "_build_native.py")
            )["build_native"]
            build(
                Path(self.build_lib) / "agent_procutil" / "native" /
                f"linux-{platform.machine()}" / "daemon-instance"
            )


class NativeDistribution(Distribution):
    def has_ext_modules(self):
        return SUPPORTED


class NativeWheel(bdist_wheel):
    def get_tag(self):
        interpreter, abi, target = super().get_tag()
        return ("py3", "none", target) if SUPPORTED else (interpreter, abi, target)


setup(
    cmdclass={"build_py": BuildPy, "bdist_wheel": NativeWheel},
    distclass=NativeDistribution,
)
