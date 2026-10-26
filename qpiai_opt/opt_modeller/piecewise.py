"""Piecewise-linear relationship descriptors."""

from __future__ import annotations

from dataclasses import dataclass

from .variables import Variable


@dataclass(frozen=True)
class PiecewiseLinear:
    """Variables and coordinates of one exact SOS2 interpolation formulation."""

    name: str
    source: Variable
    output: Variable
    breakpoints: tuple[float, ...]
    values: tuple[float, ...]
    weights: tuple[Variable, ...]
