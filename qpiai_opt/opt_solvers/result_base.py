"""Shared solve-result runtime normalization and unit views."""

from __future__ import annotations

import math
import re
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .._numeric import is_finite_real

_ResultT = TypeVar("_ResultT", bound=BaseModel)


class SolveInfo(BaseModel):
    """Validated common interpretation of a solve result."""

    problem_class: Literal["LP", "MILP", "QP", "QUBO", "HUBO", "ISING"] = Field(
        description="Identified mathematical problem class."
    )
    execution_mode: Literal["local", "platform"] = Field(
        description="Location where the solve ran."
    )
    provider: str = Field(description="SDK or platform provider that ran the solve.")
    solver: str | None = Field(
        default=None,
        description="Solver name when the result identifies it.",
    )
    method: str | None = Field(
        default=None,
        description="Mathematical or sampling method when known.",
    )
    termination: Literal[
        "optimal",
        "completed",
        "limit_reached",
        "infeasible",
        "unbounded",
        "failed",
        "unknown",
    ] = Field(description="Mathematical termination condition.")
    has_usable_solution: bool = Field(
        description="Whether a source-feasible candidate is available for use."
    )
    solution_feasible: bool | None = Field(
        description="Source-model feasibility, or null when it cannot be established."
    )
    optimality_proven: bool | None = Field(
        description="Whether global optimality was proven, or null when unknown."
    )
    guarantee: Literal[
        "global_optimum",
        "best_found",
        "best_bound",
        "feasible_only",
        "infeasibility_proven",
        "unboundedness_proven",
        "none",
        "unknown",
    ] = Field(description="Strongest guarantee established by the result.")
    source_objective: float | None = Field(
        default=None,
        description="Objective in source-model coordinates when available.",
    )
    transformed: bool | None = Field(
        default=None,
        description="Whether the source was compiled or converted before solving.",
    )
    settings: dict[str, int | float | str | bool] = Field(
        default_factory=dict,
        description=(
            "Known scalar settings that describe the selected method; sensitive "
            "platform solver configuration may be omitted."
        ),
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("provider", "solver", "method", mode="before")
    @classmethod
    def validate_optional_text(cls, value, info):
        if value is None and info.field_name in {"solver", "method"}:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value.strip()

    @field_validator("source_objective", mode="before")
    @classmethod
    def validate_source_objective(cls, value):
        if value is None:
            return value
        if not is_finite_real(value):
            raise ValueError("source_objective must be finite")
        return float(value)

    @field_validator("settings", mode="before")
    @classmethod
    def validate_settings(cls, value):
        if not isinstance(value, dict):
            raise ValueError("settings must be a dictionary")
        normalized = {}
        for name, setting in value.items():
            if not isinstance(name, str) or not name:
                raise ValueError("settings keys must be non-empty strings")
            if isinstance(setting, float) and not math.isfinite(setting):
                raise ValueError("settings values must be finite")
            if not isinstance(setting, (bool, int, float, str)):
                raise ValueError("settings values must be booleans, numbers, or strings")
            normalized[name] = setting
        return normalized

    @model_validator(mode="after")
    def validate_solution_semantics(self):
        if self.has_usable_solution and self.solution_feasible is not True:
            raise ValueError("a usable solution must be feasible")
        if self.solution_feasible is True and not self.has_usable_solution:
            raise ValueError("a feasible returned solution must be usable")
        if self.termination == "optimal" and self.optimality_proven is not True:
            raise ValueError("optimal termination requires proven optimality")
        if self.optimality_proven is True and self.termination != "optimal":
            raise ValueError("proven optimality requires optimal termination")
        if (self.guarantee == "global_optimum") != (self.optimality_proven is True):
            raise ValueError("global_optimum and proven optimality must agree")
        proof_terminations = {
            "infeasibility_proven": "infeasible",
            "unboundedness_proven": "unbounded",
        }
        expected_termination = proof_terminations.get(self.guarantee)
        if expected_termination is not None and self.termination != expected_termination:
            raise ValueError(f"{self.guarantee} requires {expected_termination} termination")
        expected_proof = {
            "infeasible": "infeasibility_proven",
            "unbounded": "unboundedness_proven",
        }.get(self.termination)
        if expected_proof is not None and self.guarantee != expected_proof:
            raise ValueError(f"{self.termination} termination requires {expected_proof}")
        if self.termination == "infeasible" and self.has_usable_solution:
            raise ValueError("infeasible termination cannot include a usable solution")
        if self.guarantee in {"best_found", "feasible_only"} and not self.has_usable_solution:
            raise ValueError(f"{self.guarantee} requires a usable solution")
        return self


def _status_evidence(value: Any) -> tuple[str | None, set[str]]:
    """Return an established outcome and explicitly negated outcomes."""

    if not isinstance(value, str) or not value.strip():
        return None, set()
    words = re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip().split()

    affirmed_terms = set()
    negated_terms = set()
    negators = {"not", "non", "no", "sub"}
    for term in ("optimal", "infeasible", "unbounded", "feasible", "solution"):
        for index, word in enumerate(words):
            if word != term:
                continue
            if index > 0 and words[index - 1] in negators:
                negated_terms.add(term)
            else:
                affirmed_terms.add(term)

    if any(word in {"error", "failed", "failure"} for word in words):
        return "failed", negated_terms

    outcomes = affirmed_terms & {"optimal", "infeasible", "unbounded", "feasible"}
    if any(word in {"limit", "interrupt", "interrupted"} for word in words):
        outcomes.add("limit_reached")
    if "suboptimal" in words or ("optimal" in negated_terms and "sub" in words):
        outcomes.add("feasible")

    terminal = outcomes & {"optimal", "infeasible", "unbounded"}
    if len(terminal) > 1:
        return "failed", negated_terms
    if terminal:
        terminal_outcome = terminal.pop()
        if "limit_reached" in outcomes or (
            terminal_outcome == "infeasible" and "feasible" in outcomes
        ):
            return "failed", negated_terms
        return terminal_outcome, negated_terms
    if "limit_reached" in outcomes:
        return "limit_reached", negated_terms
    if "feasible" in outcomes:
        return "feasible", negated_terms
    return None, negated_terms


def _common_linear_outcome(
    status: Any,
    model_status: Any,
    *,
    has_solution: bool,
) -> str:
    """Interpret class-specific linear statuses in mathematical terms."""

    status_outcome, status_negated = _status_evidence(status)
    model_outcome, model_negated = _status_evidence(model_status)
    terminal = {"optimal", "infeasible", "unbounded"}

    if "failed" in {status_outcome, model_outcome}:
        return "failed"
    if has_solution and ({"solution", "feasible"} & (status_negated | model_negated)):
        return "failed"
    if status_outcome in terminal and status_outcome in model_negated:
        return "failed"
    if model_outcome in terminal and model_outcome in status_negated:
        return "failed"
    if status_outcome in terminal and model_outcome in terminal:
        if status_outcome != model_outcome:
            return "failed"
        if has_solution and status_outcome == "infeasible":
            return "failed"
        return status_outcome
    if model_outcome in terminal:
        if has_solution and model_outcome == "infeasible":
            return "failed"
        return model_outcome
    if model_outcome == "limit_reached":
        return "limit_reached"
    if model_outcome == "feasible":
        if status_outcome == "infeasible":
            return "failed"
        if status_outcome in terminal:
            return status_outcome
        if status_outcome == "limit_reached":
            return "limit_reached"
        return "feasible"
    if status_outcome in terminal:
        if has_solution and status_outcome == "infeasible":
            return "failed"
        return status_outcome
    if status_outcome == "limit_reached":
        return "limit_reached"
    if status_outcome == "feasible" or has_solution:
        return "feasible"
    return "unknown"


def _normalize_runtime_payload(payload: dict, *, fallback_s: float | None = None) -> dict:
    """Translate the solver-service timing fields at the transport boundary."""

    normalized = dict(payload)
    compute_time = normalized.pop("compute_time", None)
    solver_time = normalized.pop("solver_time", None)
    if compute_time is not None:
        if (
            normalized.get("compute_time_s") is not None
            and normalized["compute_time_s"] != compute_time
        ):
            raise ValueError("compute_time and compute_time_s must describe the same duration")
        normalized["compute_time_s"] = compute_time
    if solver_time is not None:
        if (
            normalized.get("solver_time_s") is not None
            and normalized["solver_time_s"] != solver_time
        ):
            raise ValueError("solver_time and solver_time_s must describe the same duration")
        normalized["solver_time_s"] = solver_time
    if normalized.get("compute_time_s") is None and fallback_s is not None:
        normalized["compute_time_s"] = fallback_s
    return normalized


def _validated_result_update(result: _ResultT, **updates: Any) -> _ResultT:
    """Return a result copy with every updated field revalidated."""

    payload = result.model_dump()
    payload.update(updates)
    solve_info = payload.get("solve_info")
    updated_class = payload.get("problem_class")
    if solve_info and updated_class in {"LP", "MILP", "QP"}:
        solve_info["method"] = {
            "LP": "linear-programming",
            "MILP": "mixed-integer-linear-programming",
            "QP": "convex-quadratic-programming",
        }[updated_class]
    if solve_info and "objective" in updates:
        solve_info["source_objective"] = payload.get("objective")
    if solve_info:
        solve_info["problem_class"] = updated_class
        solve_info["execution_mode"] = payload["execution_mode"]
    return type(result).model_validate(payload)


def _default_solve_info_payload(result: BaseModel) -> dict[str, Any]:
    """Infer conservative common semantics when no route metadata was attached."""

    problem_class = getattr(result, "problem_class")
    execution_mode = getattr(result, "execution_mode")
    status = getattr(result, "status", None)
    solution = getattr(result, "solution", None)
    source_objective = getattr(result, "objective", None)
    polynomial = problem_class in {"QUBO", "HUBO", "ISING"}

    provider = "qpiai-opt-local" if execution_mode == "local" else "qpiai-opt-platform"
    if execution_mode == "local":
        solver = "HiGHS" if problem_class in {"LP", "MILP", "QP"} else "native-polynomial"
    else:
        solver = (
            "QpiAI Opt MILP Solver"
            if problem_class in {"LP", "MILP"}
            else "QpiAI Opt QUBO Solver"
            if problem_class in {"QUBO", "ISING"}
            else "QpiAI Opt HUBO Solver"
            if problem_class == "HUBO"
            else None
        )
    method = None
    transformed = None
    if polynomial:
        termination = "completed"
        has_usable_solution = solution is not None
        solution_feasible = True if has_usable_solution else None
        optimality_proven = False if execution_mode == "platform" else None
        guarantee = (
            "best_found" if has_usable_solution and execution_mode == "platform" else "unknown"
        )
    else:
        has_solution = solution is not None
        outcome = _common_linear_outcome(
            status,
            getattr(result, "model_status", None),
            has_solution=has_solution,
        )
        termination = outcome if outcome != "feasible" else "completed"
        has_usable_solution = has_solution and outcome in {
            "optimal",
            "feasible",
            "limit_reached",
        }
        solution_feasible = True if has_usable_solution else None
        optimality_proven = None if outcome == "unknown" else outcome == "optimal"
        guarantee = {
            "optimal": "global_optimum",
            "feasible": "feasible_only",
            "infeasible": "infeasibility_proven",
            "unbounded": "unboundedness_proven",
        }.get(outcome, "none" if outcome in {"limit_reached", "failed"} else "unknown")

    return {
        "problem_class": problem_class,
        "execution_mode": execution_mode,
        "provider": provider,
        "solver": solver,
        "method": method,
        "termination": termination,
        "has_usable_solution": has_usable_solution,
        "solution_feasible": solution_feasible,
        "optimality_proven": optimality_proven,
        "guarantee": guarantee,
        "source_objective": source_objective,
        "transformed": transformed,
        "settings": {},
    }


def _attach_solve_info(result: _ResultT, **updates: Any) -> _ResultT:
    """Attach validated, serializable solve provenance."""

    metadata = _default_solve_info_payload(result)
    if result.solve_info is not None:
        metadata.update(result.solve_info.model_dump())
    metadata.update(updates)
    result.solve_info = SolveInfo.model_validate(metadata)
    return result


def _attach_transformed_linear_info(
    result: _ResultT,
    *,
    source_feasible: bool | None,
) -> _ResultT:
    """Restore source-model semantics after an exact linear transformation."""

    updates: dict[str, Any] = {
        "source_objective": getattr(result, "objective", None),
        "transformed": True,
    }
    if source_feasible is not None:
        updates.update(
            has_usable_solution=source_feasible,
            solution_feasible=source_feasible,
        )
        if not source_feasible:
            updates.update(
                termination="completed",
                optimality_proven=False,
                guarantee="none",
            )
    return _attach_solve_info(result, **updates)


class TimedSolveResult(BaseModel):
    """Common result identity, two measured durations, and solve provenance."""

    problem_class: str
    execution_mode: Literal["local", "platform"]
    job_id: str | None = None
    problem_id: str | None = None
    solver_time_s: float | None = Field(
        default=None, ge=0, description="Algorithm runtime in seconds, or null when unavailable."
    )
    compute_time_s: float | None = Field(
        default=None,
        ge=0,
        description="Computation including preparation and result processing, in seconds, or null when unavailable.",
    )
    solve_info: SolveInfo | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("solver_time_s", "compute_time_s", mode="before")
    @classmethod
    def validate_duration(cls, value, info):
        if value is None:
            return value
        if not is_finite_real(value) or value < 0:
            raise ValueError(f"{info.field_name} must be a finite non-negative real number")
        return float(value)

    @field_validator("job_id", "problem_id", mode="before")
    @classmethod
    def validate_identifier(cls, value, info):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_provenance(self):
        if self.execution_mode == "local" and (
            self.job_id is not None or self.problem_id is not None
        ):
            raise ValueError("local results cannot contain platform job or problem identifiers")
        if self.solve_info is not None:
            if (
                self.solve_info.problem_class != self.problem_class
                or self.solve_info.execution_mode != self.execution_mode
            ):
                raise ValueError(
                    "solve_info must describe the result's problem class and execution mode"
                )
            if (
                self.solve_info.source_objective is not None
                and self.solve_info.source_objective != getattr(self, "objective", None)
            ):
                raise ValueError("solve_info source_objective must match the result objective")
            if self.solve_info.has_usable_solution and getattr(self, "solution", None) is None:
                raise ValueError("solve_info cannot claim a usable solution when none is returned")
        return self

    def runtime(self, unit: str = "s", *, kind: str = "compute") -> float | None:
        """Return the compute or solver duration in ``s``, ``ms``, or ``us``."""

        if not isinstance(kind, str) or kind not in ("compute", "solver"):
            raise ValueError("kind must be 'compute' or 'solver'")
        if not isinstance(unit, str) or unit not in ("s", "ms", "us"):
            raise ValueError("unit must be 's', 'ms', or 'us'")
        seconds = getattr(self, f"{kind}_time_s")
        if seconds is None:
            return None
        duration = seconds * {"s": 1.0, "ms": 1_000.0, "us": 1_000_000.0}[unit]
        if not math.isfinite(duration):
            raise ValueError(f"{kind} runtime is not representable in {unit}")
        return duration

    @property
    def compute_time_ms(self) -> float | None:
        return self.runtime("ms", kind="compute")

    @property
    def compute_time_us(self) -> float | None:
        return self.runtime("us", kind="compute")

    @property
    def solver_time_ms(self) -> float | None:
        return self.runtime("ms", kind="solver")

    @property
    def solver_time_us(self) -> float | None:
        return self.runtime("us", kind="solver")

    def info(self) -> SolveInfo:
        """Return solver, method, termination, and guarantee information."""

        if self.solve_info is not None:
            return self.solve_info.model_copy(deep=True)
        return SolveInfo.model_validate(_default_solve_info_payload(self))
