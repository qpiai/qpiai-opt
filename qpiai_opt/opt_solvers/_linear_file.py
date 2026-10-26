"""Validated metadata for LP and MPS files."""

from __future__ import annotations

import gzip
import math
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import highspy

from .._numeric import LOCAL_SMALL_MATRIX_VALUE
from ..opt_modeller.model import _LP_BRACKET_HEADER, _LP_DECODE_NAMES

_WINDOWS = sys.platform == "win32"


def _plain_model_suffix(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".lp.gz") or name.endswith(".lp"):
        return ".lp"
    if name.endswith(".mps.gz") or name.endswith(".mps"):
        return ".mps"
    raise ValueError("Model file must end in .lp, .mps, .lp.gz, or .mps.gz")


@contextmanager
def _temporary_highs_directory(source_path: Path):
    """Yield an ASCII staging directory for bindings with narrow path handling."""

    roots: list[Path | None] = [None]
    if _WINDOWS:
        candidates = [
            *source_path.absolute().parents,
            Path(sys.executable).parent,
        ]
        for environment_name in ("SystemRoot", "PUBLIC", "ProgramData"):
            value = os.environ.get(environment_name)
            if value:
                candidates.append(Path(value))
        seen = set()
        for candidate in candidates:
            candidate = candidate / "Temp" if candidate.name.lower() == "windows" else candidate
            key = str(candidate).casefold()
            if key in seen or not str(candidate).isascii() or not candidate.is_dir():
                continue
            seen.add(key)
            roots.append(candidate)

    for root in roots:
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="qpiai-opt-linear-",
                dir=root,
            )
        except OSError:
            continue
        directory = Path(temporary.name)
        if not _WINDOWS or str(directory).isascii():
            try:
                yield directory
            finally:
                temporary.cleanup()
            return
        temporary.cleanup()

    raise ValueError("Could not create a temporary compatibility file for the linear model")


@contextmanager
def _highs_readable_path(path: Path):
    """Yield a plain path that the installed HiGHS binding can open."""

    path = Path(path)
    compressed = path.name.lower().endswith(".gz")
    unicode_windows_path = _WINDOWS and not str(path).isascii()
    if not compressed and not unicode_windows_path:
        yield path
        return

    suffix = _plain_model_suffix(path)
    with _temporary_highs_directory(path) as directory:
        readable_path = directory / f"model{suffix}"
        try:
            if compressed:
                with gzip.open(path, "rb") as source, readable_path.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
            else:
                shutil.copyfile(path, readable_path)
        except (EOFError, gzip.BadGzipFile, OSError) as exc:
            action = "decompress" if compressed else "prepare"
            raise ValueError(f"Could not {action} model file {path}") from exc
        yield readable_path


@dataclass(frozen=True)
class LinearFileMetadata:
    """Problem class, objective sense, and size read from a linear model file."""

    problem_class: Literal["LP", "MILP"]
    sense: Literal["MIN", "MAX"]
    file_bytes: int
    variables: int
    constraints: int
    nonzeros: int
    lp_names_encoded: bool = False


def _read_highs_model(path: Path) -> tuple[highspy.Highs, highspy.HighsLp, bool]:
    """Read an LP or MPS file without converting finite costs or bounds to infinity."""

    path = Path(path)
    with _highs_readable_path(path) as readable_path:
        names_encoded = False
        if readable_path.suffix.lower() == ".lp":
            with readable_path.open("rb") as source:
                names_encoded = source.readline(1000).rstrip(b"\r\n") == _LP_BRACKET_HEADER.encode()
        solver = highspy.Highs()
        solver.setOptionValue("output_flag", False)
        solver.setOptionValue("small_matrix_value", LOCAL_SMALL_MATRIX_VALUE)
        solver.setOptionValue("infinite_bound", math.inf)
        solver.setOptionValue("infinite_cost", math.inf)
        if solver.readModel(str(readable_path)) != highspy.HighsStatus.kOk:
            raise ValueError(
                f"HiGHS could not read model file {path} without changing its numeric matrix"
            )
        lp = solver.getLp()
        if names_encoded:
            lp.col_names_ = [name.translate(_LP_DECODE_NAMES) for name in lp.col_names_]
            lp.row_names_ = [name.translate(_LP_DECODE_NAMES) for name in lp.row_names_]
        return solver, lp, names_encoded


def read_linear_file_metadata(path: Path) -> LinearFileMetadata:
    """Read trustworthy classification and objective metadata through HiGHS."""

    path = Path(path)
    _, lp, names_encoded = _read_highs_model(path)
    return LinearFileMetadata(
        problem_class="MILP" if len(lp.integrality_) else "LP",
        sense="MAX" if lp.sense_ == highspy.ObjSense.kMaximize else "MIN",
        file_bytes=path.stat().st_size,
        variables=int(lp.num_col_),
        constraints=int(lp.num_row_),
        nonzeros=len(lp.a_matrix_.value_),
        lp_names_encoded=names_encoded,
    )
