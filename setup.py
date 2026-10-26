"""Build public wheels using the separately compiled local runtime."""

import os
import re
import sysconfig
from pathlib import Path

from setuptools import Distribution, setup
from setuptools.command.build_py import build_py
from setuptools.command.bdist_wheel import bdist_wheel


def runtime_tag():
    target = os.environ.get("QPIAI_OPT_RUNTIME_TAG")
    if not target:
        return None
    if not re.fullmatch(r"cp3\d+-cp3\d+-(?:win_amd64|manylinux_2_28_(?:x86_64|aarch64)|macosx_13_0_(?:x86_64|arm64))", target):
        raise ValueError("Invalid runtime wheel tag")
    python, abi, platform = target.split("-")
    if python != abi:
        raise ValueError("The runtime requires a matching CPython ABI")
    return python, abi, platform


class RuntimeWheel(bdist_wheel):
    def get_tag(self):
        return runtime_tag() or super().get_tag()


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True


class BuildWithRuntime(build_py):
    def run(self):
        suffix = sysconfig.get_config_var("EXT_SUFFIX")
        filename = f"_local_runtime{suffix}"
        configured = os.environ.get("QPIAI_OPT_RUNTIME_BINARY")
        runtime = (
            Path(configured).resolve()
            if configured
            else Path(__file__).parent / "qpiai_opt" / "opt_solvers" / filename
        )
        target = runtime_tag()
        if target:
            if not configured:
                raise ValueError("A target wheel tag requires QPIAI_OPT_RUNTIME_BINARY")
            python, abi, platform = target
            extension = ".pyd" if platform == "win_amd64" else ".so"
            if not re.fullmatch(r"_local_runtime\.(?:cp" + python[2:] + r"[-.]|cpython-" + python[2:] + r"-).+" + re.escape(extension), runtime.name):
                raise ValueError("Runtime filename does not match the requested CPython ABI")
            filename = runtime.name
        if runtime.name != filename or not runtime.is_file():
            raise RuntimeError(
                "Building qpiai-opt requires its separately compiled local runtime for "
                "this Python version and platform. Set QPIAI_OPT_RUNTIME_BINARY to that "
                "binary, or install an official qpiai-opt wheel."
            )
        super().run()
        destination = Path(self.build_lib) / "qpiai_opt" / "opt_solvers" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.copy_file(str(runtime), str(destination))


setup(distclass=BinaryDistribution, cmdclass={"build_py": BuildWithRuntime, "bdist_wheel": RuntimeWheel})
