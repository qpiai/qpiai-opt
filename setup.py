"""Build public wheels using the separately compiled local runtime."""

import os
import sysconfig
from pathlib import Path

from setuptools import Distribution, setup
from setuptools.command.build_py import build_py


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


setup(distclass=BinaryDistribution, cmdclass={"build_py": BuildWithRuntime})
