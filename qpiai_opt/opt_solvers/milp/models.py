"""Validated LP/MILP platform options, diagnostics, and results."""

from __future__ import annotations

import math
import numbers
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_serializer,
    field_validator,
    model_validator,
)

from ..._numeric import is_finite_real
from ...opt_modeller.model import Model
from ..result_base import TimedSolveResult


def _finite_result_number(value, description, *, non_negative=False):
    if not is_finite_real(value):
        raise ValueError(f"{description} must be a finite real number")
    normalized = float(value)
    if non_negative and normalized < 0:
        raise ValueError(f"{description} must be non-negative")
    return normalized


def _finite_result_mapping(value, description):
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a dictionary")
    result = {}
    for name, number in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{description} keys must be non-empty strings")
        result[name] = _finite_result_number(number, f"{description}[{name!r}]")
    return result


def _ranging_mapping(value, description):
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a dictionary")
    result = {}
    for name, number in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{description} keys must be non-empty strings")
        if number is None:
            result[name] = None
        elif isinstance(number, str):
            if number not in {"+Inf", "-Inf"}:
                raise ValueError(
                    f"{description}[{name!r}] must be a number, '+Inf', '-Inf', or null"
                )
            result[name] = number
        elif is_finite_real(number):
            result[name] = float(number)
        else:
            raise ValueError(f"{description}[{name!r}] must be a number, '+Inf', '-Inf', or null")
    return result


class MILPSolverConfig(BaseModel):
    """Platform LP/MILP solver configuration."""

    # ── Termination ──────────────────────────────────────────────────────
    time_limit: float = Field(
        default=300.0,
        ge=0,
        description=(
            "Wall-clock limit in seconds. Zero leaves the limit unset. A limited "
            "solve may return an incumbent without proving optimality."
        ),
    )
    mip_max_nodes: int = Field(
        default=-1,
        strict=True,
        ge=-1,
        description=("Maximum branch-and-bound nodes. Minus one leaves the node limit unset."),
    )
    solution_limit: int = Field(
        default=-1,
        strict=True,
        ge=-1,
        description=(
            "Stop after finding this many feasible MIP solutions. -1 = unlimited. "
            "A value of 1 stops after the first feasible solution."
        ),
    )
    objective_bound: float | None = Field(
        default=None,
        description=(
            "Stop when the best dual bound reaches this value. "
            "For minimization, the condition is lower bound >= value."
        ),
    )
    objective_target: float | None = Field(
        default=None,
        description=(
            "Stop when a feasible solution with this objective (or better) is found. "
            "For minimization, the condition is incumbent <= target."
        ),
    )

    # ── Tolerances ───────────────────────────────────────────────────────
    mip_rel_gap: float = Field(
        default=1e-4,
        ge=0,
        description=(
            "Relative MIP optimality gap (0.0001 = 0.01%). "
            "The solver may stop when |incumbent - bound| / |incumbent| <= value."
        ),
    )
    mip_abs_gap: float = Field(
        default=1e-6,
        ge=0,
        description=("Absolute MIP gap. The solver may stop when |incumbent - bound| <= value."),
    )
    primal_feasibility_tolerance: float = Field(
        default=1e-7,
        gt=0,
        description=("Maximum primal constraint violation accepted by the solver."),
    )
    dual_feasibility_tolerance: float = Field(
        default=1e-7,
        gt=0,
        description="Maximum dual feasibility violation accepted for LP.",
    )
    mip_feasibility_tolerance: float = Field(
        default=1e-6,
        gt=0,
        description=(
            "Integer feasibility tolerance. A value is integral when |x - round(x)| <= tolerance."
        ),
    )

    # ── Algorithm ────────────────────────────────────────────────────────
    threads: int = Field(
        default=0,
        strict=True,
        ge=0,
        description=(
            "Thread count. 0 = auto (all cores). Set a positive value to bound solver threads."
        ),
    )
    presolve: Literal["on", "off", "choose"] = Field(
        default="choose",
        description=(
            "'on' enables presolve, 'off' disables it, and 'choose' delegates the "
            "choice to the solver."
        ),
    )
    solver_algorithm: Literal["simplex", "ipm", "choose"] = Field(
        default="choose",
        description=("LP algorithm: simplex, interior-point method, or solver-selected."),
    )
    mip_heuristic_effort: float = Field(
        default=0.05,
        ge=0,
        le=1,
        description=("Solver heuristic-effort setting from 0.0 through 1.0."),
    )

    # ── Output ───────────────────────────────────────────────────────────
    output_flag: bool = Field(
        default=False,
        strict=True,
        description="Solver console output. True = verbose, False = silent.",
    )
    log_file: str | None = Field(
        default=None,
        description="Path to solver log file. None = no file logging.",
    )

    # ── Feature flags ────────────────────────────────────────────────────
    include_dual_info: bool = Field(
        default=False,
        strict=True,
        description=(
            "Include dual values (shadow prices) and reduced costs. "
            "Only meaningful for LP or LP relaxation of MIP."
        ),
    )
    compute_ranging: bool = Field(
        default=False,
        strict=True,
        description=(
            "Compute sensitivity analysis after solving. Shows how much each "
            "objective coefficient and RHS can change before the basis changes. "
            "Requires a simplex basis."
        ),
    )
    compute_iis: bool = Field(
        default=True,
        strict=True,
        description=(
            "On infeasibility, compute an Irreducible Infeasible Set. "
            "The result identifies a conflicting constraint and bound subset."
        ),
    )
    collect_solution_pool: bool = Field(
        default=False,
        strict=True,
        description=(
            "Collect all improving MIP solutions via callbacks. "
            "Memory grows with solutions x variables."
        ),
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("presolve", "solver_algorithm", mode="before")
    @classmethod
    def normalize_enum(cls, value):
        return value.lower() if isinstance(value, str) else value

    @field_validator(
        "time_limit",
        "objective_bound",
        "objective_target",
        "mip_rel_gap",
        "mip_abs_gap",
        "primal_feasibility_tolerance",
        "dual_feasibility_tolerance",
        "mip_feasibility_tolerance",
        "mip_heuristic_effort",
        mode="before",
    )
    @classmethod
    def validate_finite_number(cls, value, info):
        if value is None and info.field_name in {"objective_bound", "objective_target"}:
            return value
        if not is_finite_real(value):
            raise ValueError(f"{info.field_name} must be a finite real number")
        return value

    @field_validator("solution_limit")
    @classmethod
    def validate_solution_limit(cls, value):
        if value == 0:
            raise ValueError("solution_limit must be -1 or a positive integer")
        return value

    @field_validator("log_file")
    @classmethod
    def validate_log_file(cls, value):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("log_file must be a non-empty string or None")
        return value


def _model_ir(model):
    from ...opt_modeller.ir import to_ir

    return to_ir(model)


class MILPProblem(BaseModel):
    """An LP/MILP model or file, with optional solver configuration.

    A model is retained in memory as an independent canonical snapshot.
    Platform execution exports that snapshot only for the upload operation.
    """

    problem_type: Literal["LP", "MILP"] = "MILP"
    config: MILPSolverConfig | None = None
    model_path: Path | None = None
    model: Annotated[
        Model | None,
        PlainSerializer(lambda value: None if value is None else _model_ir(value)),
        WithJsonSchema(
            {"anyOf": [{"type": "object", "description": "Canonical model IR."}, {"type": "null"}]}
        ),
    ] = None

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    @field_serializer("config")
    def serialize_config(self, value):
        return None if value is None else value.model_dump(exclude_unset=True)

    @model_validator(mode="before")
    @classmethod
    def validate_representation(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        model = data.get("model")
        if isinstance(model, dict):
            from ...opt_modeller.ir import from_ir

            model = from_ir(model)
        path = data.get("model_path")
        if (model is None) == (path is None):
            raise ValueError("provide exactly one of model or model_path")
        if model is not None:
            if not isinstance(model, Model):
                raise TypeError("model must be a Model or canonical model IR")
            from ..local import _validated_linear_arrays

            model = deepcopy(model)
            model._validate_linear()
            matrix = model.to_lp_matrix(for_milp=True)
            _validated_linear_arrays(matrix, allow_empty=True)
            actual = matrix["problem_type"]
            if actual == "MIP":
                actual = "MILP"
            data["model"] = model
        else:
            if not isinstance(path, (str, Path)):
                raise ValueError("model_path must be an LP/MPS file path")
            path = Path(path)
            if not path.name.lower().endswith((".lp", ".mps", ".lp.gz", ".mps.gz")):
                raise ValueError("model_path must end in .lp, .mps, .lp.gz, or .mps.gz")
            from .._linear_file import read_linear_file_metadata

            actual = read_linear_file_metadata(path).problem_class
            data["model_path"] = path
        declared = data.get("problem_type", actual)
        if not isinstance(declared, str) or declared.upper() != actual:
            raise ValueError(f"problem_type must match the model's {actual} class")
        data["problem_type"] = actual
        return data

    @classmethod
    def from_model(cls, model, *, config=None):
        """Validate and retain a complete in-memory LP/MILP snapshot."""

        return cls(model=model, config=config)

    def _execution_input(self, options, *, mode):
        options = dict(options)
        if self.config is not None:
            config = MILPSolverConfig.model_validate(self.config.model_dump(exclude_unset=True))
            if mode == "platform":
                options.setdefault("config", config)
            else:
                unsupported = config.model_fields_set - {
                    "time_limit",
                    "mip_max_nodes",
                    "mip_rel_gap",
                }
                if unsupported:
                    raise ValueError(
                        "Local LP/MILP does not support configuration fields: "
                        + ", ".join(sorted(unsupported))
                    )
                if config.time_limit == 0:
                    raise ValueError("Local LP/MILP requires a positive time_limit")
                options.setdefault("time_limit", config.time_limit)
                if self.problem_type == "MILP":
                    options.setdefault("mip_rel_gap", config.mip_rel_gap)
                    if config.mip_max_nodes != -1:
                        if config.mip_max_nodes == 0:
                            raise ValueError("Local MILP requires a positive node limit")
                        options.setdefault("node_limit", config.mip_max_nodes)
                elif {"mip_max_nodes", "mip_rel_gap"} & config.model_fields_set:
                    raise ValueError("MIP gap and node limits apply only to MILP")
        return self.model if self.model is not None else self.model_path, options

    def _as_model(self):
        if self.model is not None:
            return self.model
        import highspy

        from ...opt_modeller.expressions import quicksum
        from ...opt_modeller.variables import Continuous, Integer, SemiContinuous, SemiInteger
        from .._linear_file import _read_highs_model
        from ..local import _linear_arrays_from_highs_lp

        _, lp, _ = _read_highs_model(self.model_path)
        arrays = _linear_arrays_from_highs_lp(lp, highspy)
        model = Model(self.model_path.stem)
        constructors = (Continuous, Integer, SemiContinuous, SemiInteger)
        variables = [
            constructors[int(code)](name, lower, upper)
            for name, code, lower, upper in zip(
                arrays.names, arrays.integrality, arrays.lower_bounds, arrays.upper_bounds
            )
        ]
        model.add_variables(variables)
        objective = (
            quicksum(
                coefficient * variable for coefficient, variable in zip(arrays.objective, variables)
            )
            + arrays.offset
        )
        model.maximize(objective) if arrays.sense == "MAX" else model.minimize(objective)
        matrix = arrays.matrix.tocsr()
        row_names = list(lp.row_names_) or [f"c{index}" for index in range(len(arrays.row_lower))]
        used_labels = set(row_names)
        for index, (lower, upper) in enumerate(zip(arrays.row_lower, arrays.row_upper)):
            row = matrix[index : index + 1, :]
            expression = quicksum(
                coefficient * variables[column]
                for coefficient, column in zip(row.data, row.indices)
            )
            label = row_names[index]
            if lower == upper:
                model.add_constraint(expression == lower, label=label)
            else:
                for bound, operation, suffix in (
                    (lower, expression.__ge__, "lower"),
                    (upper, expression.__le__, "upper"),
                ):
                    if not math.isfinite(bound):
                        continue
                    bound_label = label
                    if math.isfinite(lower) and math.isfinite(upper):
                        bound_label = f"{label}_{suffix}"
                        while bound_label in used_labels:
                            bound_label += "_"
                        used_labels.add(bound_label)
                    model.add_constraint(operation(bound), label=bound_label)
        return model


class WarmStart(BaseModel):
    """Initial solution or basis to seed the solver."""

    col_values: dict[str, float] | None = Field(
        default=None,
        description=(
            "MIP-start values keyed by variable name. Omitted variables form a partial start."
        ),
    )
    col_basis_status: list[str] | None = Field(
        default=None,
        description=(
            "Column basis status from a previous solve. "
            "Values: 'lower', 'basic', 'upper', 'zero', 'nonbasic'."
        ),
    )
    row_basis_status: list[str] | None = Field(
        default=None,
        description=(
            "Row basis status from a previous solve. "
            "Values: 'lower', 'basic', 'upper', 'zero', 'nonbasic'."
        ),
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("col_values", mode="before")
    @classmethod
    def validate_col_values(cls, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("Warm-start column values must be a dictionary")
        for name, number in value.items():
            if not isinstance(name, str) or not name:
                raise ValueError("Warm-start variable names must be non-empty strings")
            if not is_finite_real(number):
                raise ValueError(f"Warm-start value for {name!r} must be finite")
        return {name: float(number) for name, number in value.items()}

    @field_validator("col_basis_status", "row_basis_status")
    @classmethod
    def validate_basis_statuses(cls, value, info):
        if value is None:
            return value
        allowed = {"lower", "basic", "upper", "zero", "nonbasic"}
        normalized = []
        for status in value:
            if not isinstance(status, str) or status.lower() not in allowed:
                raise ValueError(f"{info.field_name} values must be one of {sorted(allowed)!r}")
            normalized.append(status.lower())
        return normalized

    @model_validator(mode="after")
    def validate_contents(self):
        if (self.col_basis_status is None) != (self.row_basis_status is None):
            raise ValueError("col_basis_status and row_basis_status must be provided together")
        if self.col_values is None and self.col_basis_status is None:
            raise ValueError("WarmStart requires column values or a complete basis")
        return self


class BasisData(BaseModel):
    """LP basis for warm-starting future solves."""

    col_status: list[str]
    row_status: list[str]

    model_config = ConfigDict(extra="forbid")

    @field_validator("col_status", "row_status", mode="before")
    @classmethod
    def validate_statuses(cls, value, info):
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{info.field_name} must be a list")
        allowed = {"lower", "basic", "upper", "zero", "nonbasic"}
        result = []
        for status in value:
            if not isinstance(status, str) or status.lower() not in allowed:
                raise ValueError(f"{info.field_name} values must be one of {sorted(allowed)!r}")
            result.append(status.lower())
        return result


class PoolSolution(BaseModel):
    """A feasible MIP solution from the solution pool."""

    objective: float
    values: list[float]

    model_config = ConfigDict(extra="forbid")

    @field_validator("objective", mode="before")
    @classmethod
    def validate_objective(cls, value):
        return _finite_result_number(value, "objective")

    @field_validator("values", mode="before")
    @classmethod
    def validate_values(cls, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("values must be a list")
        return [_finite_result_number(item, f"values[{index}]") for index, item in enumerate(value)]


class IISData(BaseModel):
    """Irreducible Infeasible Set — minimal conflicting subset."""

    row_indices: list[int]
    row_names: list[str]
    row_bounds: list[int]
    col_indices: list[int]
    col_names: list[str]
    col_bounds: list[int]

    model_config = ConfigDict(extra="forbid")

    @field_validator("row_indices", "row_bounds", "col_indices", "col_bounds", mode="before")
    @classmethod
    def validate_integer_lists(cls, value, info):
        if not isinstance(value, (list, tuple)) or any(
            isinstance(item, bool) or not isinstance(item, numbers.Integral) for item in value
        ):
            raise ValueError(f"{info.field_name} must contain integers")
        normalized = [int(item) for item in value]
        if info.field_name in {"row_indices", "col_indices"} and any(
            item < 0 for item in normalized
        ):
            raise ValueError(f"{info.field_name} cannot contain negative indices")
        return normalized

    @field_validator("row_names", "col_names", mode="before")
    @classmethod
    def validate_names(cls, value, info):
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise ValueError(f"{info.field_name} must contain non-empty strings")
        return list(value)

    @model_validator(mode="after")
    def validate_parallel_arrays(self):
        if not (
            len(self.row_indices) == len(self.row_names) == len(self.row_bounds)
            and len(self.col_indices) == len(self.col_names) == len(self.col_bounds)
        ):
            raise ValueError("IIS index, name, and bound arrays must have matching lengths")
        return self


class RangingData(BaseModel):
    """Sensitivity analysis: how much parameters can change before basis changes."""

    variables: dict[str, dict[str, float | Literal["+Inf", "-Inf"] | None]]
    constraints: dict[str, dict[str, float | Literal["+Inf", "-Inf"] | None]]

    model_config = ConfigDict(extra="forbid")

    @field_validator("variables", "constraints", mode="before")
    @classmethod
    def validate_ranges(cls, value, info):
        if not isinstance(value, dict):
            raise ValueError(f"{info.field_name} must be a dictionary")
        result = {}
        for name, ranges in value.items():
            if not isinstance(name, str) or not name or not isinstance(ranges, dict):
                raise ValueError(f"{info.field_name} must map non-empty names to dictionaries")
            result[name] = _ranging_mapping(
                ranges,
                f"{info.field_name}[{name!r}]",
            )
        return result


class SolveResult(TimedSolveResult):
    """Complete solver output, JSON-serializable for API responses."""

    problem_class: Literal["LP", "MILP"] = "MILP"
    execution_mode: Literal["local", "platform"] = "platform"
    sense: Literal["MIN", "MAX"] | None = None
    status: str
    model_status: str
    objective: float | None = None
    num_nodes: int | None = Field(default=0, strict=True, ge=0)
    best_bound: float | None = None
    optimality_gap: float | None = None
    iteration_count: int = Field(default=0, strict=True, ge=0)
    error_message: str | None = None

    solution: dict[str, float] | None = None
    dual_values: dict[str, float] | None = None
    reduced_costs: dict[str, float] | None = None

    basis: BasisData | None = None
    ranging: RangingData | None = None
    solution_pool: list[PoolSolution] | None = None
    iis: IISData | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("num_nodes", mode="before")
    @classmethod
    def normalize_unavailable_node_count(cls, value):
        return None if type(value) is int and value == -1 else value

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("status must be a non-empty string")
        return value.strip().lower()

    @field_validator("model_status", mode="before")
    @classmethod
    def validate_model_status(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("model_status must be a non-empty string")
        return value.strip()

    @field_validator(
        "objective",
        "best_bound",
        "optimality_gap",
        mode="before",
    )
    @classmethod
    def validate_optional_numbers(cls, value, info):
        if value is None:
            return value
        return _finite_result_number(
            value,
            info.field_name,
            non_negative=info.field_name == "optimality_gap",
        )

    @field_validator("solution", "dual_values", "reduced_costs", mode="before")
    @classmethod
    def validate_numeric_mappings(cls, value, info):
        if value is None:
            return value
        return _finite_result_mapping(value, info.field_name)

    @model_validator(mode="after")
    def validate_incumbent_and_bound(self):
        if self.objective is None or self.best_bound is None:
            return self

        scale = max(1.0, abs(self.objective), abs(self.best_bound))
        difference = abs(self.objective - self.best_bound)
        matches = difference <= 1e-10 * scale
        if self.sense == "MIN" and self.best_bound > self.objective and not matches:
            raise ValueError("MIN best_bound cannot exceed the incumbent objective")
        if self.sense == "MAX" and self.best_bound < self.objective and not matches:
            raise ValueError("MAX best_bound cannot be below the incumbent objective")
        if self.status.lower() == "optimal" and not matches:
            raise ValueError("an optimal result requires matching objective and best_bound")

        expected_gap = 0.0 if matches else None
        if not matches and self.objective != 0.0:
            expected_gap = difference / abs(self.objective)
        if self.optimality_gap is not None and (
            expected_gap is None
            or not math.isclose(self.optimality_gap, expected_gap, rel_tol=1e-10, abs_tol=1e-12)
        ):
            raise ValueError("optimality_gap is inconsistent with objective and best_bound")
        return self


class JobSubmissionResponse(BaseModel):
    """Non-blocking MILP submission details."""

    job_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled", "unknown"]
    message: str
    problem_id: str
    storage_id: str

    model_config = ConfigDict(extra="forbid")

    @field_validator("job_id", "status", "message", "problem_id", "storage_id", mode="before")
    @classmethod
    def validate_text(cls, value, info):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value.strip().lower() if info.field_name == "status" else value.strip()


class JobStatusResponse(BaseModel):
    """Normalized MILP job state with a validated result when completed."""

    job_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled", "unknown"]
    result: SolveResult | None = None
    error: str | None = None
    submitted_at: str | None = None
    completed_at: str | None = None
    compute_time_s: float | None = None
    solver_time_s: float | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("job_id", "status", "submitted_at", "completed_at", mode="before")
    @classmethod
    def validate_required_text(cls, value, info):
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
