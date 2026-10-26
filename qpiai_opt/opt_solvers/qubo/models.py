"""Validated QUBO platform request and result models."""

from __future__ import annotations

import math
import numbers
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from ..._numeric import is_finite_real
from ..result_base import TimedSolveResult


class QUBOProblem(BaseModel):
    """Validated QUBO problem specification for the platform.

    The stored matrix is always fully symmetric. Set ``matrix_format="upper"``
    when supplying direct polynomial coefficients; off-diagonal values are then
    split equally across their two symmetric matrix positions.
    """

    problem_type: Literal["QUBO"] = "QUBO"
    num_variables: int = Field(..., strict=True, ge=0)
    matrix_format: Literal["symmetric", "upper"] = "symmetric"
    max_evaluations: int = Field(default=10000, strict=True, gt=0)
    solver_config: Optional[Dict[str, Any]] = None
    sense: Literal["MIN", "MAX"] = "MIN"
    offset: float = 0.0
    variable_names: Optional[Tuple[str, ...]] = None
    qubo_dict: Dict[Tuple[int, int], float]
    _source_model: Any = PrivateAttr(default=None)

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_model(
        cls,
        model,
        *,
        max_evaluations=10000,
        solver_config=None,
        **compile_options,
    ):
        """Create a platform problem from a modeller ``Model`` without sign ambiguity."""

        from ...opt_modeller import Model

        if not isinstance(model, Model):
            raise TypeError("from_model expects a qpiai_opt.opt_modeller.Model")
        with model._compilation_transaction():
            qubo, compiled_offset = model.to_qubo_dict(
                matrix_format="symmetric",
                **compile_options,
            )
            if model.sense == "MAX":
                source_qubo = {key: -coefficient for key, coefficient in qubo.items()}
                source_offset = -compiled_offset
            else:
                source_qubo = qubo
                source_offset = compiled_offset
            problem = cls(
                num_variables=len(model.qubo_variable_names),
                qubo_dict=source_qubo,
                max_evaluations=max_evaluations,
                solver_config=solver_config,
                offset=source_offset,
                sense=model.sense,
                variable_names=tuple(model.qubo_variable_names),
            )
            from copy import deepcopy

            problem._source_model = deepcopy(model)
            return problem

    @field_validator("matrix_format", mode="before")
    @classmethod
    def normalize_matrix_format(cls, value):
        if not isinstance(value, str):
            raise ValueError("matrix_format must be 'symmetric' or 'upper'")
        normalized = value.strip().lower().replace("-", "_")
        normalized = {
            "fully_symmetric": "symmetric",
            "upper_triangular": "upper",
        }.get(normalized, normalized)
        if normalized not in {"symmetric", "upper"}:
            raise ValueError("matrix_format must be 'symmetric' or 'upper'")
        return normalized

    @field_validator("problem_type", mode="before")
    @classmethod
    def normalize_problem_type(cls, value):
        if not isinstance(value, str) or value.upper() != "QUBO":
            raise ValueError("problem_type must be 'QUBO'")
        return "QUBO"

    @field_validator("sense", mode="before")
    @classmethod
    def normalize_sense(cls, value):
        if not isinstance(value, str) or value.upper() not in {"MIN", "MAX"}:
            raise ValueError("sense must be 'MIN' or 'MAX'")
        return value.upper()

    @field_validator("offset")
    @classmethod
    def validate_offset(cls, value):
        if not math.isfinite(value):
            raise ValueError("offset must be finite")
        return value

    @model_validator(mode="before")
    @classmethod
    def validate_raw_qubo(cls, value):
        if not isinstance(value, dict):
            return value
        qubo = value.get("qubo_dict")
        if not isinstance(qubo, dict):
            raise ValueError("qubo_dict must be a dictionary")
        offset = value.get("offset", 0.0)
        if not is_finite_real(offset):
            raise ValueError("offset must be a finite real number")
        for key, coefficient in qubo.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or any(
                    not isinstance(index, numbers.Integral) or isinstance(index, bool)
                    for index in key
                )
            ):
                raise ValueError(f"QUBO key {key!r} must be a pair of integer indices")
            if not is_finite_real(coefficient):
                raise ValueError(f"QUBO coefficient for {key!r} must be finite")
        return value

    @model_validator(mode="after")
    def validate_and_symmetrize(self):
        normalized = {}
        for key, coefficient in self.qubo_dict.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or any(
                    not isinstance(index, numbers.Integral) or isinstance(index, bool)
                    for index in key
                )
            ):
                raise ValueError(f"QUBO key {key!r} must be a pair of integer indices")
            left, right = map(int, key)
            if not (0 <= left < self.num_variables and 0 <= right < self.num_variables):
                raise ValueError(f"QUBO key {key!r} is outside [0, {self.num_variables})")
            if not is_finite_real(coefficient):
                raise ValueError(f"QUBO coefficient for {key!r} must be finite")
            normalized[(left, right)] = float(coefficient)

        if self.matrix_format == "upper":
            lower_keys = [key for key in normalized if key[0] > key[1]]
            if lower_keys:
                raise ValueError(
                    f"Upper-triangular QUBO input contains lower-triangle keys: {lower_keys!r}"
                )
            symmetric = {}
            for (left, right), coefficient in normalized.items():
                if coefficient == 0.0:
                    continue
                if left == right:
                    symmetric[(left, right)] = coefficient
                else:
                    half = coefficient / 2.0
                    if half == 0.0:
                        raise ValueError(
                            "QUBO symmetrization underflowed an off-diagonal "
                            "coefficient; rescale the problem"
                        )
                    symmetric[(left, right)] = half
                    symmetric[(right, left)] = half
            self.qubo_dict = symmetric
            self.matrix_format = "symmetric"
        else:
            for (left, right), coefficient in normalized.items():
                if left == right or coefficient == 0.0:
                    continue
                reverse = (right, left)
                if reverse not in normalized:
                    raise ValueError(f"Fully symmetric QUBO is missing mirrored key {reverse!r}")
                if normalized[reverse] != coefficient:
                    raise ValueError(
                        f"Fully symmetric QUBO values differ at {(left, right)!r} and {reverse!r}"
                    )
            self.qubo_dict = {
                key: coefficient for key, coefficient in normalized.items() if coefficient != 0.0
            }

        try:
            coefficient_scale = math.fsum(
                [abs(float(self.offset)), *(abs(value) for value in self.qubo_dict.values())]
            )
        except OverflowError as exc:
            raise ValueError("QUBO coefficient scale overflowed; rescale the problem") from exc
        if not math.isfinite(coefficient_scale):
            raise ValueError("QUBO coefficient scale overflowed; rescale the problem")

        if self.variable_names is not None:
            if (
                len(self.variable_names) != self.num_variables
                or len(set(self.variable_names)) != self.num_variables
                or any(
                    not name or any(char in name for char in "\x00\r\n")
                    for name in self.variable_names
                )
            ):
                raise ValueError(
                    "variable_names must contain one unique non-empty single-line name per variable"
                )
        return self


def _validated_qubo_problem(problem) -> QUBOProblem:
    """Return a fresh validated snapshot of a QUBO input."""

    if isinstance(problem, QUBOProblem):
        validated = QUBOProblem.model_validate(problem.model_dump())
        validated._source_model = problem._source_model
        return validated
    return QUBOProblem.model_validate(problem)


class SolveResult(TimedSolveResult):
    """Solver result from the QUBO service."""

    problem_class: Literal["QUBO"] = "QUBO"
    execution_mode: Literal["local", "platform"] = "platform"
    num_nodes: int = Field(..., strict=True, ge=0)
    objective: float
    variable_assignment: Dict[str, Union[int, float]]

    solution: List[int]

    model_config = ConfigDict(extra="forbid")

    @field_validator("objective", mode="before")
    @classmethod
    def validate_finite_result_number(cls, value, info):
        if not is_finite_real(value):
            raise ValueError(f"{info.field_name} must be finite")
        return value

    @field_validator("solution", mode="before")
    @classmethod
    def validate_binary_solution(cls, value):
        if not isinstance(value, (list, tuple)) or any(
            item not in (0, 1, False, True) for item in value
        ):
            raise ValueError("solution must contain only binary values")
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
        if len(self.solution) != self.num_nodes:
            raise ValueError(
                f"solution contains {len(self.solution)} values for {self.num_nodes} nodes"
            )
        return self


class JobSubmissionResponse(BaseModel):
    """Response from submitting a QUBO job."""

    job_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled", "unknown"]
    message: str
    problem_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("job_id", "status", "message", "problem_id", mode="before")
    @classmethod
    def validate_text(cls, value, info):
        if value is None and info.field_name == "problem_id":
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value.strip().lower() if info.field_name == "status" else value.strip()


class JobStatusResponse(BaseModel):
    """Asynchronous job state and, when completed, its result."""

    job_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled", "unknown"]
    result: Optional[SolveResult] = None
    error: Optional[str] = None
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None
    compute_time_s: Optional[float] = None
    solver_time_s: Optional[float] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("job_id", "status", "submitted_at", "completed_at", mode="before")
    @classmethod
    def validate_text(cls, value, info):
        if value is None and info.field_name in {"submitted_at", "completed_at"}:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value.strip().lower() if info.field_name == "status" else value.strip()

    @field_validator("compute_time_s", "solver_time_s", mode="before")
    @classmethod
    def validate_timing(cls, value, info):
        if value is None:
            return value
        if not is_finite_real(value) or value < 0:
            raise ValueError(f"{info.field_name} must be a finite non-negative number")
        return float(value)

    @model_validator(mode="after")
    def validate_result_lifecycle(self):
        if self.status == "completed" and self.result is None:
            raise ValueError("a completed job must include its result")
        if self.status != "completed" and self.result is not None:
            raise ValueError("only a completed job may include a result")
        return self
