import runpy
from pathlib import Path

from setuptools import setup

commands = runpy.run_path(str(Path(__file__).resolve().with_name("_build_runtime_resource.py")))

setup(cmdclass={"build_py": commands["BuildPy"], "sdist": commands["BuildSdist"]})
