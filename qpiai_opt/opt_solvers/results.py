"""Local QP and Ising results plus the public solve-result union."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Union

from pydantic import ConfigDict, Field, field_validator, model_validator

from .._numeric import is_finite_real
from .result_base import TimedSolveResult


class IsingSolveResult(TimedSolveResult):
    """Completed Ising solve result."""

    problem_class: Literal["ISING"] = "ISING"
    execution_mode: Literal["local", "platform"]
    num_variables: int = Field(..., strict=True, ge=0)
    objective: float
    variable_assignment: Dict[str, Union[int, float]]

    solution: List[int]

    model_config = ConfigDict(extra="forbid")

    @field_validator("objective", mode="before")
    @classmethod
    def validate_finite_number(cls, value, info):
        if not is_finite_real(value):
            raise ValueError(f"{info.field_name} must be finite")
        normalized = float(value)
        return normalized

    @field_validator("solution", mode="before")
    @classmethod
    def validate_spin_solution(cls, value):
        if not isinstance(value, (list, tuple)) or any(
            not is_finite_real(item) or item not in (-1, 1) for item in value
        ):
            raise ValueError("solution must contain only -1 and +1 spin values")
        return [int(item) for item in value]

    @field_validator("variable_assignment", mode="before")
    @classmethod
    def validate_variable_assignment(cls, value):
        if not isinstance(value, dict) or any(
            not isinstance(name, str) or not name for name in value
        ):
            raise ValueError("variable_assignment must map non-empty variable names to values")
        normalized = {}
        for name, item in value.items():
            if not is_finite_real(item):
                raise ValueError("variable_assignment values must be finite numbers")
            normalized[name] = int(item) if float(item).is_integer() else float(item)
        return normalized

    @model_validator(mode="after")
    def validate_solution_size(self):
        if len(self.solution) != self.num_variables:
            raise ValueError(
                f"solution contains {len(self.solution)} values for {self.num_variables} variables"
            )
        return self


class QPSolveResult(TimedSolveResult):
    """Continuous quadratic-program solve result."""

    problem_class: Literal["QP"] = "QP"
    execution_mode: Literal["local"] = "local"
    sense: Literal["MIN", "MAX"]
    status: str
    model_status: str
    objective: Optional[float] = None
    solution: Optional[Dict[str, float]] = None
    iteration_count: int = Field(default=0, strict=True, ge=0)

    model_config = ConfigDict(extra="forbid")

    @field_validator("status", "model_status", mode="before")
    @classmethod
    def validate_status_text(cls, value, info):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        normalized = value.strip()
        return normalized.lower() if info.field_name == "status" else normalized

    @field_validator("objective", mode="before")
    @classmethod
    def validate_objective(cls, value):
        if value is None:
            return value
        if not is_finite_real(value):
            raise ValueError("objective must be finite")
        return float(value)

    @field_validator("solution", mode="before")
    @classmethod
    def validate_solution(cls, value):
        if value is None:
            return value
        if not isinstance(value, dict) or any(
            not isinstance(name, str) or not name for name in value
        ):
            raise ValueError("solution must map non-empty variable names to values")
        normalized = {}
        for name, item in value.items():
            if not is_finite_real(item):
                raise ValueError("solution values must be finite numbers")
            normalized[name] = float(item)
        return normalized


from .hubo.models import SolveResult as HUBOSolveResult  # noqa: E402
from .milp.models import SolveResult as MILPSolveResult  # noqa: E402
from .qubo.models import SolveResult as QUBOSolveResult  # noqa: E402

SolveResult = Union[
    QUBOSolveResult,
    HUBOSolveResult,
    IsingSolveResult,
    QPSolveResult,
    MILPSolveResult,
]
