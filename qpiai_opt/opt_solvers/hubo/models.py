"""Validated HUBO platform request, configuration, and result models."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from ..._numeric import fsum_by_key, is_finite_real
from ..result_base import TimedSolveResult

SOLVER_CONFIG_PARAMS = {
    "num_sweeps",
    "num_reads",
    "beta_min",
    "beta_max",
    "num_threads",
    "temperature_schedule",
    "seed",
}


def validate_solver_config_values(value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Validate and normalize supported HUBO solver configuration values."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("solver_config must be a dictionary")
    value = dict(value)

    unknown_keys = set(value) - SOLVER_CONFIG_PARAMS
    if unknown_keys:
        allowed = ", ".join(sorted(SOLVER_CONFIG_PARAMS))
        unknown = ", ".join(sorted(unknown_keys))
        raise ValueError(
            f"Unsupported solver configuration keys: {unknown}. Allowed keys: {allowed}"
        )
    null_keys = sorted(key for key, item in value.items() if item is None)
    if null_keys:
        raise ValueError(
            "Solver configuration values cannot be None; omit unset keys: " + ", ".join(null_keys)
        )

    for key in ("num_sweeps", "num_reads", "num_threads"):
        if key in value and (
            isinstance(value[key], bool) or not isinstance(value[key], int) or value[key] < 1
        ):
            raise ValueError(f"{key} must be a positive integer")

    for key in ("beta_min", "beta_max"):
        if key in value and (not is_finite_real(value[key]) or value[key] <= 0):
            raise ValueError(f"{key} must be a positive finite number")

    if "beta_min" in value and "beta_max" in value and value["beta_min"] > value["beta_max"]:
        raise ValueError("beta_min cannot exceed beta_max")

    if "temperature_schedule" in value:
        if not isinstance(value["temperature_schedule"], str):
            raise ValueError("temperature_schedule must be 'LINEAR' or 'GEOMETRIC'")
        value["temperature_schedule"] = value["temperature_schedule"].upper()
        if value["temperature_schedule"] not in ("LINEAR", "GEOMETRIC"):
            raise ValueError("temperature_schedule must be 'LINEAR' or 'GEOMETRIC'")

    if "seed" in value and (isinstance(value["seed"], bool) or not isinstance(value["seed"], int)):
        raise ValueError("seed must be an integer")

    return value


def _label_sort_key(label):
    return type(label).__name__, repr(label)


class HUBOProblem(BaseModel):
    """Validated binary HUBO problem specification for the platform."""

    problem_type: Literal["HUBO"] = "HUBO"
    num_variables: int = Field(..., strict=True, ge=0)
    sense: Literal["MIN", "MAX"] = "MIN"
    vartype: Literal["BINARY"] = "BINARY"
    solver_config: Optional[Dict[str, Any]] = None
    variable_names: Optional[Tuple[str, ...]] = None
    polynomial_dict: Dict[Tuple[str, ...], float]
    _source_model: Any = PrivateAttr(default=None)

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_model(cls, model, *, solver_config=None, **compile_options):
        """Compile and retain an independent source and decoding snapshot."""

        from ...opt_modeller.model import Model

        if not isinstance(model, Model):
            raise TypeError("from_model expects a qpiai_opt.opt_modeller.Model")
        with model._compilation_transaction():
            polynomial = model.to_hubo_dict(**compile_options)
            sign = -1.0 if model.sense == "MAX" else 1.0
            problem = cls(
                num_variables=len(model.hubo_variable_names),
                polynomial_dict={term: sign * value for term, value in polynomial.items()},
                variable_names=tuple(model.hubo_variable_names),
                sense=model.sense,
                solver_config=solver_config,
            )
            problem._source_model = deepcopy(model)
            return problem

    @field_validator("sense", "problem_type", "vartype", mode="before")
    @classmethod
    def normalize_enum(cls, value, info):
        expected = {
            "sense": {"MIN", "MAX"},
            "problem_type": {"HUBO"},
            "vartype": {"BINARY"},
        }[info.field_name]
        if not isinstance(value, str) or value.upper() not in expected:
            choices = " or ".join(repr(item) for item in sorted(expected))
            raise ValueError(f"{info.field_name} must be {choices}")
        return value.upper()

    @field_validator("solver_config")
    @classmethod
    def validate_solver_config(cls, value):
        return validate_solver_config_values(value)

    @model_validator(mode="before")
    @classmethod
    def validate_raw_polynomial(cls, value):
        if not isinstance(value, dict):
            return value
        polynomial = value.get("polynomial_dict")
        if not isinstance(polynomial, dict):
            raise ValueError("polynomial_dict must be a dictionary")

        coefficient_contributions = []
        for term, coefficient in polynomial.items():
            if not isinstance(term, tuple):
                raise ValueError(f"HUBO term {term!r} must be a tuple")
            if not is_finite_real(coefficient):
                raise ValueError(f"HUBO coefficient for {term!r} must be finite")

            seen = set()
            canonical = []
            for label in term:
                if (
                    not isinstance(label, str)
                    or not label
                    or any(char in label for char in "\x00\r\n")
                ):
                    raise ValueError("HUBO labels must be non-empty single-line strings")
                identity = (type(label), label)
                if identity in seen:
                    continue
                seen.add(identity)
                canonical.append(label)

            canonical_term = tuple(sorted(canonical, key=_label_sort_key))
            coefficient_contributions.append((canonical_term, float(coefficient)))

        try:
            totals = fsum_by_key(coefficient_contributions)
            normalized = {
                term: coefficient for term, coefficient in totals.items() if coefficient != 0.0
            }
        except OverflowError as exc:
            raise ValueError(
                "HUBO coefficient aggregation overflowed; rescale the problem"
            ) from exc

        try:
            coefficient_scale = math.fsum(abs(value) for value in normalized.values())
        except OverflowError as exc:
            raise ValueError("HUBO coefficient scale overflowed; rescale the problem") from exc
        if not math.isfinite(coefficient_scale):
            raise ValueError("HUBO coefficient scale overflowed; rescale the problem")

        canonical_labels = {label for term in normalized for label in term}
        raw_count = value.get("num_variables")
        raw_names = value.get("variable_names")
        if raw_names is not None:
            if not isinstance(raw_names, (tuple, list)) or any(
                not isinstance(name, str) or not name or any(char in name for char in "\x00\r\n")
                for name in raw_names
            ):
                raise ValueError("variable_names must contain non-empty single-line strings")
            if len(set(raw_names)) != len(raw_names) or len(raw_names) != raw_count:
                raise ValueError("variable_names must contain one unique name per variable")
            if not canonical_labels <= set(raw_names):
                raise ValueError("variable_names must cover every polynomial variable")
        elif (
            isinstance(raw_count, int)
            and not isinstance(raw_count, bool)
            and raw_count != len(canonical_labels)
        ):
            raise ValueError(
                f"num_variables is {raw_count}, but the polynomial contains "
                f"{len(canonical_labels)} distinct variables after canonicalization"
            )

        updated = dict(value)
        updated["polynomial_dict"] = normalized
        return updated


def _validated_hubo_problem(problem) -> HUBOProblem:
    """Return a fresh validated snapshot of a HUBO input."""

    data = problem.model_dump() if isinstance(problem, HUBOProblem) else problem
    validated = HUBOProblem.model_validate(data)
    if isinstance(problem, HUBOProblem):
        validated._source_model = problem._source_model
    return validated


class SolveResult(TimedSolveResult):
    """Solver result from the HUBO service."""

    problem_class: Literal["HUBO"] = "HUBO"
    execution_mode: Literal["local", "platform"] = "platform"
    num_variables: int = Field(..., strict=True, ge=0)
    objective: float
    variable_assignment: Dict[str, Union[int, float]]

    solution: List[int]

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "objective",
        mode="before",
    )
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
        if not isinstance(value, dict):
            raise ValueError("variable_assignment must be a variable-to-value dictionary")
        normalized = {}
        for label, item in value.items():
            if not isinstance(label, str) or not label:
                raise ValueError("variable_assignment labels must be non-empty strings")
            if not is_finite_real(item):
                raise ValueError("variable_assignment values must be finite numbers")
            normalized[label] = int(item) if float(item).is_integer() else float(item)
        return normalized

    @model_validator(mode="after")
    def validate_solution_size(self):
        if len(self.solution) != self.num_variables:
            raise ValueError(
                f"solution contains {len(self.solution)} values for {self.num_variables} variables"
            )
        return self


class JobSubmissionResponse(BaseModel):
    """Response from submitting a HUBO job."""

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
    solver_time_s: Optional[float] = None
    compute_time_s: Optional[float] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "job_id",
        "status",
        "submitted_at",
        "completed_at",
        mode="before",
    )
    @classmethod
    def validate_text(cls, value, info):
        if value is None and info.field_name not in {"job_id", "status"}:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value.strip().lower() if info.field_name == "status" else value.strip()

    @field_validator(
        "solver_time_s",
        "compute_time_s",
        mode="before",
    )
    @classmethod
    def validate_optional_measurement(cls, value, info):
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
