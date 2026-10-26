"""Class-specific local solving, limits, and result restoration."""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable

import numpy as np
from scipy import sparse

from .._numeric import (
    BINARY64_EXACT_INTEGER_LIMIT,
    DEFAULT_FEASIBILITY_TOLERANCE,
    LOCAL_FINITE_VALUE_LIMIT,
    LOCAL_LARGE_MATRIX_VALUE,
    LOCAL_SMALL_MATRIX_VALUE,
    finite_fsum,
    fsum_by_key,
    is_finite_real,
)
from ..opt_modeller import Model
from ..opt_modeller.expressions import _finite_product
from ..opt_modeller.utils.exceptions import InvalidExpressionError, NumericalStabilityError
from .client import Client
from .exceptions import AuthenticationError, LocalLimitExceeded
from .hubo.models import HUBOProblem
from .hubo.models import SolveResult as HUBOSolveResult
from .ising import IsingProblem
from .milp.models import SolveResult as MILPSolveResult
from .providers import ProviderCapabilities
from .qubo.models import QUBOProblem
from .qubo.models import SolveResult as QUBOSolveResult
from .result_base import _attach_solve_info
from .results import IsingSolveResult, QPSolveResult, SolveResult

LOCAL_LIMITS: Mapping[str, Mapping[str, int]] = MappingProxyType(
    {
        name: MappingProxyType(limits)
        for name, limits in {
            "LP": {
                "variables": 20_000,
                "constraints": 20_000,
                "nonzeros": 1_000_000,
                "file_bytes": 50_000_000,
            },
            "MILP": {
                "variables": 20_000,
                "constraints": 20_000,
                "nonzeros": 1_000_000,
                "file_bytes": 50_000_000,
            },
            "QP": {
                "variables": 1_000,
                "constraints": 20_000,
                "nonzeros": 1_000_000,
                "hessian_nonzeros": 100_000,
            },
            "QUBO": {
                "variables": 250,
                "terms": 5_000,
                "num_reads": 16,
                "num_sweeps": 5_000,
                "annealing_work": 10_000_000,
            },
            "HUBO": {
                "variables": 100,
                "terms": 1_500,
                "num_reads": 16,
                "num_sweeps": 2_500,
                "annealing_work": 10_000_000,
            },
            "ISING": {
                "variables": 250,
                "terms": 5_000,
                "num_reads": 16,
                "num_sweeps": 5_000,
                "annealing_work": 10_000_000,
            },
        }.items()
    }
)

NATIVE_EXACT_MAX_VARIABLES = 20
NATIVE_EXACT_MAX_WORK = 2_000_000
DEFAULT_POLYNOMIAL_NUM_READS = 8
DEFAULT_POLYNOMIAL_NUM_SWEEPS = 500
DEFAULT_POLYNOMIAL_SEED = 0
_LOCAL_POLYNOMIAL_CONFIG_KEYS = frozenset({"num_reads", "num_sweeps", "seed"})
_LOCAL_RUNTIME_INSTALL_ERROR = (
    "The compiled QpiAI local runtime is unavailable. "
    "Install an official qpiai-opt wheel for your Python version and platform."
)

_LINEAR_MATRIX_KEYS = {
    "name",
    "problem_type",
    "method",
    "solver",
    "format",
    "sense",
    "variable_names",
    "objective",
    "objective_coefficients",
    "objective_offset",
    "constraint_names",
    "constraint_matrix",
    "row_lower",
    "row_upper",
    "constraints",
    "lower_bounds",
    "upper_bounds",
    "integrality",
    "variable_types",
    "sos",
    "sos_reformulated",
}
_LINEAR_SHORTHAND_KEYS = {
    "problem_type",
    "method",
    "c",
    "A",
    "b_l",
    "b_u",
    "lower_bounds",
    "upper_bounds",
    "integrality",
    "variable_names",
    "sense",
    "objective_offset",
}


@dataclass
class _Polynomial:
    terms: dict[tuple[str, ...], float]
    names: list[str]
    offset: float
    sense: str
    vartype: str
    finalize: Callable[[dict[str, int], float], tuple[dict[str, float | int], float]]
    transformed: bool = False
    source_feasibility: Callable[[dict[str, float | int]], bool] | None = None


@dataclass
class _LinearArrays:
    objective: np.ndarray
    matrix: sparse.csc_array
    names: list[str]
    row_lower: np.ndarray
    row_upper: np.ndarray
    lower_bounds: np.ndarray
    upper_bounds: np.ndarray
    integrality: np.ndarray
    sense: str
    offset: float


def _limit_error(problem_class: str, observed: dict[str, int]) -> None:
    limits = LOCAL_LIMITS[problem_class]
    exceeded = {
        metric: value
        for metric, value in observed.items()
        if metric in limits and value > limits[metric]
    }
    if not exceeded:
        return

    def display_value(value: int) -> str:
        if isinstance(value, int) and value.bit_length() > 128:
            return "an extremely large integer"
        return str(value)

    detail = ", ".join(
        f"{metric}={display_value(value)} exceeds {limits[metric]}"
        for metric, value in exceeded.items()
    )
    if any(name in exceeded for name in ("num_reads", "num_sweeps", "annealing_work")):
        next_action = (
            "reduce num_reads, num_sweeps, or model interactions; otherwise call "
            "solve(..., mode='platform') explicitly, which may consume credits"
        )
    else:
        next_action = (
            "reduce the model or call solve(..., mode='platform') explicitly; platform use may "
            "consume credits"
        )
    raise LocalLimitExceeded(
        problem_class,
        observed={
            metric: display_value(value)
            if isinstance(value, int) and value.bit_length() > 128
            else value
            for metric, value in observed.items()
        },
        limits=limits,
        explanation=detail,
        next_action=next_action,
    )


def _validate_local_sampling_settings(num_reads: int, num_sweeps: int, seed: int) -> None:
    for name, value in (("num_reads", num_reads), ("num_sweeps", num_sweeps)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an integer between 0 and 2**32 - 1")


def _local_stored_config(problem: Any) -> dict[str, int]:
    """Return stored QUBO/HUBO settings supported by the native local solver."""

    if not isinstance(problem, (QUBOProblem, HUBOProblem)) or not problem.solver_config:
        return {}
    config = dict(problem.solver_config)
    unsupported = set(config) - _LOCAL_POLYNOMIAL_CONFIG_KEYS
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(
            f"{problem.problem_type} solver_config contains options unsupported by the local "
            "native solver: "
            f"{names}. Remove them or use mode='platform'."
        )
    _validate_local_sampling_settings(
        config.get("num_reads", DEFAULT_POLYNOMIAL_NUM_READS),
        config.get("num_sweeps", DEFAULT_POLYNOMIAL_NUM_SWEEPS),
        config.get("seed", DEFAULT_POLYNOMIAL_SEED),
    )
    return config


def _identity_finalize(
    assignment: dict[str, int], objective: float
) -> tuple[dict[str, float | int], float]:
    return assignment, objective


def _qubo_polynomial(
    problem: QUBOProblem,
    *,
    include_isolated: bool = True,
) -> _Polynomial:
    if include_isolated:
        names = list(
            problem.variable_names or tuple(f"x[{index}]" for index in range(problem.num_variables))
        )
        names_by_index = names
    else:
        active_indices = sorted({index for key in problem.qubo_dict for index in key})
        names = [
            problem.variable_names[index] if problem.variable_names else f"x[{index}]"
            for index in active_indices
        ]
        names_by_index = dict(zip(active_indices, names))
    contributions = []
    for (left, right), coefficient in problem.qubo_dict.items():
        term = (
            (names_by_index[left],)
            if left == right
            else tuple(sorted((names_by_index[left], names_by_index[right])))
        )
        contributions.append((term, float(coefficient)))
    try:
        totals = fsum_by_key(contributions)
        terms = {term: value for term, value in totals.items() if value != 0.0}
    except OverflowError as exc:
        raise ValueError("QUBO coefficient aggregation overflowed; rescale the problem") from exc
    polynomial = _Polynomial(
        terms=terms,
        names=names,
        offset=float(problem.offset),
        sense=problem.sense,
        vartype="BINARY",
        finalize=_identity_finalize,
    )
    if include_isolated and problem._source_model is not None:
        source_model = problem._source_model

        def finalize(assignment, objective):
            indexed = [assignment[name] for name in names]
            decoded = source_model.decode_qubo_sample(indexed)
            return decoded, float(source_model.evaluate_objective(decoded))

        polynomial.finalize = finalize
        polynomial.transformed = True
        polynomial.source_feasibility = source_model.is_feasible
    return polynomial


def _hubo_polynomial(problem: HUBOProblem) -> _Polynomial:
    labels = (
        list(problem.variable_names)
        if problem.variable_names is not None
        else sorted(
            {label for term in problem.polynomial_dict for label in term},
            key=lambda value: (type(value).__name__, repr(value)),
        )
    )
    internal = {label: f"x[{index}]" for index, label in enumerate(labels)}
    reverse = {name: label for label, name in internal.items()}
    contributions = []
    for raw_term, coefficient in problem.polynomial_dict.items():
        term = tuple(sorted({internal[label] for label in raw_term}))
        contributions.append((term, float(coefficient)))
    try:
        aggregated = fsum_by_key(contributions)
        offset = finite_fsum([aggregated.get((), 0.0)])
    except OverflowError as exc:
        raise ValueError("HUBO coefficient aggregation overflowed; rescale the problem") from exc

    def finalize(assignment, objective):
        labelled = {reverse[name]: value for name, value in assignment.items()}
        if problem._source_model is not None:
            decoded = problem._source_model.decode_hubo_sample(labelled)
            return decoded, float(problem._source_model.evaluate_objective(decoded))
        return labelled, objective

    return _Polynomial(
        terms={term: value for term, value in aggregated.items() if term and value != 0.0},
        names=list(internal.values()),
        offset=offset,
        sense=problem.sense,
        vartype="BINARY",
        finalize=finalize,
        transformed=problem._source_model is not None,
        source_feasibility=problem._source_model.is_feasible
        if problem._source_model is not None
        else None,
    )


def _ising_polynomial(
    problem: IsingProblem,
    *,
    include_isolated: bool = True,
) -> _Polynomial:
    if include_isolated:
        names = list(
            problem.variable_names or tuple(f"s[{index}]" for index in range(problem.num_variables))
        )
        names_by_index = names
    else:
        active_indices = sorted(
            set(problem.linear) | {index for key in problem.quadratic for index in key}
        )
        names = [
            problem.variable_names[index] if problem.variable_names else f"s[{index}]"
            for index in active_indices
        ]
        names_by_index = dict(zip(active_indices, names))
    terms = {(names_by_index[index],): coefficient for index, coefficient in problem.linear.items()}
    terms.update(
        {
            tuple(sorted((names_by_index[left], names_by_index[right]))): coefficient
            for (left, right), coefficient in problem.quadratic.items()
        }
    )

    def finalize(assignment, objective):
        spins = [assignment[name] for name in names]
        return problem.decode(spins), float(problem.source_objective(spins))

    return _Polynomial(
        terms={term: value for term, value in terms.items() if value != 0.0},
        names=names,
        offset=float(problem.offset),
        sense=problem.sense,
        vartype="SPIN",
        finalize=finalize,
        transformed=problem._source_model is not None,
        source_feasibility=problem._source_model.is_feasible
        if problem._source_model is not None
        else None,
    )


def _model_polynomial(model: Model, problem_class: str) -> _Polynomial:
    if problem_class == "QUBO":
        validated = QUBOProblem.from_model(model)
        polynomial = _qubo_polynomial(validated)
        return polynomial
    if problem_class == "HUBO":
        terms = model.to_hubo_dict()
        names = list(model.hubo_variable_names)
        polynomial = _Polynomial(
            terms={tuple(map(str, term)): float(value) for term, value in terms.items() if term},
            names=names,
            offset=float(terms.get((), 0.0)),
            sense="MIN",
            vartype="BINARY",
            finalize=_identity_finalize,
        )

        def finalize(assignment, objective):
            original = model.decode_hubo_sample(assignment)
            return original, float(model.evaluate_objective(original))

        polynomial.finalize = finalize
        polynomial.transformed = True
        polynomial.source_feasibility = model.is_feasible
        return polynomial
    if problem_class == "ISING":
        polynomial = _ising_polynomial(model.compile(target="ISING"))
        polynomial.transformed = True
        polynomial.source_feasibility = model.is_feasible
        return polynomial
    raise TypeError(f"{problem_class} is not a polynomial problem class")


def _polynomial_energy(polynomial: _Polynomial, assignment: Mapping[str, int]) -> float:
    """Evaluate one native-domain assignment with the offset in the same sum."""

    contributions = [polynomial.offset] + [
        coefficient * math.prod(assignment[name] for name in term)
        for term, coefficient in polynomial.terms.items()
    ]
    try:
        return finite_fsum(contributions)
    except OverflowError as exc:
        raise ValueError("Polynomial energy evaluation overflowed") from exc


def _polynomial_result(
    problem_class: str,
    polynomial: _Polynomial,
    raw_assignment: dict[str, int],
    variable_assignment: dict,
    objective: float,
    time_us: float,
    *,
    exact: bool,
    settings: dict[str, int],
) -> SolveResult:
    solution = [raw_assignment[name] for name in polynomial.names]
    common = {
        "objective": objective,
        "solver_time_s": time_us / 1_000_000.0,
        "execution_mode": "local",
        "solution": solution,
        "variable_assignment": variable_assignment,
    }
    if problem_class == "QUBO":
        result = QUBOSolveResult(num_nodes=len(polynomial.names), **common)
    elif problem_class == "HUBO":
        result = HUBOSolveResult(num_variables=len(polynomial.names), **common)
    else:
        result = IsingSolveResult(num_variables=len(polynomial.names), **common)

    source_feasible = (
        polynomial.source_feasibility(variable_assignment)
        if polynomial.source_feasibility is not None
        else True
    )
    return _attach_solve_info(
        result,
        solver="native-polynomial",
        method="exhaustive-enumeration" if exact else "simulated-annealing",
        termination="optimal" if exact and source_feasible else "completed",
        has_usable_solution=source_feasible,
        solution_feasible=source_feasible,
        optimality_proven=exact and source_feasible,
        guarantee=(
            "global_optimum"
            if exact and source_feasible
            else "best_found"
            if source_feasible
            else "none"
        ),
        transformed=polynomial.transformed,
        settings=settings,
    )


def _polynomial_metrics(polynomial: _Polynomial) -> dict[str, int]:
    active_variables = len(_active_polynomial_names(polynomial))
    interaction_order = sum(len(term) for term in polynomial.terms)
    return {
        "variables": len(polynomial.names),
        "active_variables": active_variables,
        "terms": len(polynomial.terms),
        "degree": max((len(term) for term in polynomial.terms), default=0),
        "exact_enumeration_work": 2**active_variables
        * max(1, active_variables + interaction_order),
    }


def _active_polynomial_names(polynomial: _Polynomial) -> list[str]:
    active = {name for term in polynomial.terms for name in term}
    return [name for name in polynomial.names if name in active]


def _annealing_work(polynomial: _Polynomial, num_reads: int, num_sweeps: int) -> int:
    """Estimate native annealing work from the operations performed per term."""

    active_variables = len(_active_polynomial_names(polynomial))
    if active_variables == 0:
        return 0
    initial_energy_work = sum(max(1, len(term)) for term in polynomial.terms)
    sweep_work = active_variables + sum(
        len(term) * max(1, len(term) - 1) for term in polynomial.terms
    )
    return num_reads * (initial_energy_work + num_sweeps * sweep_work)


def _qp_source_hessian_nonzeros(model: Model) -> int:
    """Count Hessian entries represented by the source quadratic terms."""

    entries = set()
    for term in model.objective.terms:
        if len(term.variables) != 2:
            continue
        left, right = (id(variable) for variable in term.variables)
        entries.add((left, right))
        entries.add((right, left))
    return len(entries)


def _scipy_status(status: int, has_solution: bool) -> str:
    if status == 0:
        return "optimal" if has_solution else "failed"
    if status == 1:
        return "feasible" if has_solution else "limit_reached"
    if status == 2:
        return "infeasible"
    if status == 3:
        return "unbounded"
    return "failed"


def _attach_local_linear_info(
    result: MILPSolveResult | QPSolveResult,
    *,
    termination: str,
    transformed: bool,
) -> MILPSolveResult | QPSolveResult:
    """Attach common semantics once the class-specific result is validated."""

    usable = result.solution is not None and result.status in {"optimal", "feasible", "unbounded"}
    if result.status in {"optimal", "infeasible", "unbounded", "failed"}:
        termination = result.status
    elif result.status == "feasible" and termination != "limit_reached":
        termination = "completed"
    if result.status == "optimal":
        guarantee = "global_optimum"
    elif result.status == "infeasible":
        guarantee = "infeasibility_proven"
    elif result.status == "unbounded":
        guarantee = "unboundedness_proven"
    elif result.status in {"feasible", "limit_reached"} and (
        getattr(result, "best_bound", None) is not None
    ):
        guarantee = "best_bound"
    elif usable:
        guarantee = "feasible_only"
    else:
        guarantee = "none"
    method = {
        "LP": "linear-programming",
        "MILP": "mixed-integer-linear-programming",
        "QP": "convex-quadratic-programming",
    }[result.problem_class]
    return _attach_solve_info(
        result,
        provider="qpiai-opt-local",
        solver="HiGHS",
        method=method,
        termination=termination,
        has_usable_solution=usable,
        solution_feasible=True if usable else None,
        optimality_proven=result.status == "optimal",
        guarantee=guarantee,
        transformed=transformed,
        settings={},
    )


def _bound_matches_objective(objective: float | None, bound: float | None) -> bool:
    if objective is None or bound is None:
        return False
    scale = max(1.0, abs(objective), abs(bound))
    return abs(objective - bound) <= 1e-10 * scale


def _source_relative_gap(objective: float | None, bound: float | None) -> float | None:
    """Return the source-coordinate relative gap, or None when it is undefined."""

    if objective is None or bound is None:
        return None
    difference = abs(objective - bound)
    if _bound_matches_objective(objective, bound):
        return 0.0
    if objective == 0.0:
        return None
    return difference / abs(objective)


def _validate_bound_direction(objective: float | None, bound: float | None, sense: str) -> None:
    """Reject a dual bound that is on the incumbent side of the objective."""

    if objective is None or bound is None or _bound_matches_objective(objective, bound):
        return
    wrong_side = bound > objective if sense == "MIN" else bound < objective
    if wrong_side:
        raise RuntimeError(
            f"Solver returned a {sense} bound on the wrong side of the incumbent objective"
        )


def _normalized_linear_solution(
    arrays: _LinearArrays,
    raw_values: Any,
    *,
    tolerance: float = DEFAULT_FEASIBILITY_TOLERANCE,
) -> np.ndarray | None:
    """Return a feasible, domain-normalized solver assignment or ``None``."""

    try:
        values = np.asarray(raw_values, dtype=float)
    except (TypeError, ValueError):
        return None
    if values.shape != arrays.objective.shape or not np.all(np.isfinite(values)):
        return None
    values = values.copy()

    for index, code in enumerate(arrays.integrality):
        value = values[index]
        lower = arrays.lower_bounds[index]
        upper = arrays.upper_bounds[index]
        if code in (2, 3) and abs(value) <= tolerance:
            values[index] = 0.0
            continue
        if value < lower - tolerance or value > upper + tolerance:
            return None
        if value < lower:
            values[index] = lower
        elif value > upper:
            values[index] = upper
        if code in (1, 3):
            nearest = round(float(values[index]))
            if abs(values[index] - nearest) > tolerance:
                return None
            values[index] = nearest

    with np.errstate(over="ignore", invalid="ignore"):
        activities = np.asarray(arrays.matrix @ values, dtype=float)
    if not np.all(np.isfinite(activities)):
        return None
    for activity, lower, upper in zip(activities, arrays.row_lower, arrays.row_upper):
        scale = max(1.0, abs(float(activity)))
        if math.isfinite(lower):
            scale = max(scale, abs(float(lower)))
            if activity < lower - tolerance * scale:
                return None
        if math.isfinite(upper):
            scale = max(scale, abs(float(upper)))
            if activity > upper + tolerance * scale:
                return None
    return values


def _highs_has_feasible_solution(solution: Any, info: Any, highspy: Any) -> bool:
    """Return whether HiGHS marked the stored primal values as feasible."""

    if not bool(solution.value_valid):
        return False
    primal_status = getattr(info, "primal_solution_status", None)
    feasible_status = getattr(highspy, "kSolutionStatusFeasible", None)
    return primal_status is None or feasible_status is None or primal_status == feasible_status


def _linear_arrays_from_highs_lp(lp: Any, highspy: Any) -> _LinearArrays:
    """Build the common validation view from a model parsed by HiGHS."""

    count = int(lp.num_col_)
    row_count = int(lp.num_row_)
    matrix_data = np.asarray(lp.a_matrix_.value_, dtype=float)
    matrix_indices = np.asarray(lp.a_matrix_.index_, dtype=np.int32)
    matrix_starts = np.asarray(lp.a_matrix_.start_, dtype=np.int32)
    if lp.a_matrix_.format_ == highspy.MatrixFormat.kColwise:
        matrix = sparse.csc_array(
            (matrix_data, matrix_indices, matrix_starts),
            shape=(row_count, count),
        )
    elif lp.a_matrix_.format_ == highspy.MatrixFormat.kRowwise:
        matrix = sparse.csr_array(
            (matrix_data, matrix_indices, matrix_starts),
            shape=(row_count, count),
        ).tocsc()
    else:
        raise RuntimeError("HiGHS returned an unsupported constraint-matrix format")

    raw_integrality = list(lp.integrality_)
    if raw_integrality and len(raw_integrality) != count:
        raise RuntimeError("HiGHS returned incomplete variable-domain metadata")
    integrality = np.zeros(count, dtype=np.int32)
    for index, variable_type in enumerate(raw_integrality):
        code = int(variable_type)
        integrality[index] = 1 if code == 4 else code
    if not np.all(np.isin(integrality, (0, 1, 2, 3))):
        raise RuntimeError("HiGHS returned an unsupported variable domain")

    names = list(lp.col_names_)
    if not names:
        names = [f"x[{index}]" for index in range(count)]
    if len(names) != count or len(set(names)) != count or any(not name for name in names):
        raise RuntimeError("HiGHS returned invalid variable names")
    arrays = _LinearArrays(
        objective=np.asarray(lp.col_cost_, dtype=float),
        matrix=matrix,
        names=names,
        row_lower=np.asarray(lp.row_lower_, dtype=float),
        row_upper=np.asarray(lp.row_upper_, dtype=float),
        lower_bounds=np.asarray(lp.col_lower_, dtype=float),
        upper_bounds=np.asarray(lp.col_upper_, dtype=float),
        integrality=integrality,
        sense="MAX" if lp.sense_ == highspy.ObjSense.kMaximize else "MIN",
        offset=float(lp.offset_),
    )
    _validate_linear_numeric_range(arrays)
    return arrays


def _linear_data(problem: Model | Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if isinstance(problem, Model):
        return problem.to_lp_matrix(for_milp=True), "Model"
    data = dict(problem)
    if "objective" in data:
        unknown = set(data) - _LINEAR_MATRIX_KEYS
        required = {"objective", "constraint_matrix", "row_lower", "row_upper"}
    else:
        unknown = set(data) - _LINEAR_SHORTHAND_KEYS
        required = {"c", "A", "b_l", "b_u"}
    if unknown:
        raise ValueError(
            "linear matrix input contains unsupported fields: "
            + ", ".join(sorted(map(str, unknown)))
        )
    missing = required - data.keys()
    if missing:
        raise ValueError(f"linear matrix input is missing: {', '.join(sorted(missing))}")
    if "objective" not in data:
        data = {
            "objective": data["c"],
            "constraint_matrix": data["A"],
            "row_lower": data["b_l"],
            "row_upper": data["b_u"],
            **{key: value for key, value in data.items() if key not in {"c", "A", "b_l", "b_u"}},
        }
    try:
        raw_objective = np.asarray(data["objective"])
    except (TypeError, ValueError) as exc:
        raise ValueError("objective must be a one-dimensional numeric array") from exc
    if raw_objective.ndim != 1:
        raise ValueError("objective must be a one-dimensional array of finite numbers")
    count = int(raw_objective.size)
    data.setdefault("lower_bounds", np.zeros(count))
    data.setdefault("upper_bounds", np.full(count, np.inf))
    data.setdefault("integrality", np.zeros(count, dtype=np.int32))
    data.setdefault("variable_names", [f"x[{i}]" for i in range(count)])
    data.setdefault("sense", "min")
    data.setdefault("objective_offset", 0.0)
    return data, "matrix"


def _validated_integrality(data: Mapping[str, Any], count: int) -> np.ndarray:
    raw = np.asarray(data["integrality"])
    if raw.ndim != 1 or raw.size != count:
        raise ValueError("integrality must contain one code per objective coefficient")
    if any(
        isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Real)
        for value in raw.flat
    ):
        raise ValueError("integrality codes must be integers 0, 1, 2, or 3")
    numeric = raw.astype(float)
    if (
        not np.all(np.isfinite(numeric))
        or not np.all(numeric == np.floor(numeric))
        or not np.all(np.isin(numeric, (0, 1, 2, 3)))
    ):
        raise ValueError("integrality codes must be integers 0, 1, 2, or 3")
    return numeric.astype(np.int32)


def _numeric_vector(value: Any, name: str, count: int) -> np.ndarray:
    try:
        raw = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a one-dimensional numeric array") from exc
    if raw.shape != (count,):
        raise ValueError(f"{name} must contain {count} values")
    if any(
        isinstance(item, (bool, np.bool_)) or not isinstance(item, numbers.Real)
        for item in raw.flat
    ):
        raise ValueError(f"{name} must contain numbers, not booleans or numeric strings")
    if raw.dtype.kind in "iu" and raw.dtype.itemsize >= 8:
        if raw.dtype.kind == "u":
            inexact = np.any(raw > BINARY64_EXACT_INTEGER_LIMIT)
        else:
            inexact = np.any(
                (raw > BINARY64_EXACT_INTEGER_LIMIT) | (raw < -BINARY64_EXACT_INTEGER_LIMIT)
            )
        if inexact:
            raise ValueError(
                f"{name} contains an integer outside binary64's exact range "
                f"(±{BINARY64_EXACT_INTEGER_LIMIT}); use a scaled model"
            )
    if raw.dtype.kind == "O" or (raw.dtype.kind == "f" and raw.dtype.itemsize > 8):
        normalized = []
        for item in raw.flat:
            try:
                converted = float(item)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{name} values must be representable as binary64 numbers"
                ) from exc
            is_integral_value = isinstance(item, numbers.Integral) or (
                isinstance(item, numbers.Rational) and getattr(item, "denominator", None) == 1
            )
            if is_integral_value and math.isfinite(converted):
                if int(converted) != int(item):
                    raise ValueError(
                        f"{name} contains an integer outside binary64's exact range "
                        f"(±{BINARY64_EXACT_INTEGER_LIMIT}); use a scaled model"
                    )
            if converted == 0.0 and item != 0:
                raise ValueError(f"{name} contains a nonzero value not representable as binary64")
            normalized.append(converted)
        return np.asarray(normalized, dtype=float)
    return raw.astype(float)


def _linear_objective(arrays: _LinearArrays, values) -> float:
    try:
        return finite_fsum(
            [
                arrays.offset,
                *(
                    _finite_product(float(cost), float(value), "Linear objective evaluation")
                    for cost, value in zip(arrays.objective, values)
                ),
            ]
        )
    except (InvalidExpressionError, OverflowError) as exc:
        raise NumericalStabilityError(
            "Linear objective is not representable; rescale the model"
        ) from exc


def _accurate_sparse_matrix(value: Any) -> sparse.csc_array:
    """Convert a matrix to CSC while accurately combining duplicate coordinates."""

    sparse_input = sparse.issparse(value)
    if sparse_input and getattr(value, "has_canonical_format", False):
        if len(value.shape) != 2:
            raise ValueError("constraint_matrix must be a rectangular numeric matrix")
        coefficients = _numeric_vector(value.data, "constraint_matrix", value.data.size)
        if not np.all(np.isfinite(coefficients)):
            raise ValueError("constraint_matrix coefficients must be finite")
        matrix = sparse.csc_array(value, dtype=float, copy=True)
        matrix.eliminate_zeros()
        return matrix
    try:
        raw = value.tocoo(copy=False) if sparse_input else sparse.coo_array(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("constraint_matrix must be a rectangular numeric matrix") from exc
    if len(raw.shape) != 2:
        raise ValueError("constraint_matrix must be a rectangular numeric matrix")
    coefficients = _numeric_vector(raw.data, "constraint_matrix", raw.data.size)
    if not np.all(np.isfinite(coefficients)):
        raise ValueError("constraint_matrix coefficients must be finite")
    rows = np.asarray(raw.row, dtype=np.int64)
    columns = np.asarray(raw.col, dtype=np.int64)
    if len(coefficients) and sparse_input:
        order = np.lexsort((columns, rows))
        rows = rows[order]
        columns = columns[order]
        coefficients = coefficients[order]
        starts = np.r_[
            0,
            np.flatnonzero((rows[1:] != rows[:-1]) | (columns[1:] != columns[:-1])) + 1,
        ]
        stops = np.r_[starts[1:], len(coefficients)]
        totals = coefficients[starts].copy()
        try:
            for index in np.flatnonzero(stops - starts > 1):
                totals[index] = finite_fsum(coefficients[starts[index] : stops[index]])
        except OverflowError as exc:
            raise ValueError("constraint_matrix coefficient aggregation overflowed") from exc
        nonzero = totals != 0.0
        rows = rows[starts][nonzero]
        columns = columns[starts][nonzero]
        coefficients = totals[nonzero]
    return sparse.csc_array((coefficients, (rows, columns)), shape=raw.shape)


def _validate_finite_magnitude(values: np.ndarray, name: str) -> None:
    if np.any(np.isfinite(values) & (np.abs(values) >= LOCAL_FINITE_VALUE_LIMIT)):
        raise ValueError(
            f"{name} contains a finite value with magnitude at or above "
            f"{LOCAL_FINITE_VALUE_LIMIT:g}; rescale the model"
        )


def _validate_linear_numeric_range(arrays: _LinearArrays) -> None:
    if not np.all(np.isfinite(arrays.objective)):
        raise ValueError("objective must be a one-dimensional array of finite numbers")
    _validate_finite_magnitude(arrays.objective, "objective")

    for name, lower, upper in (
        ("row bounds", arrays.row_lower, arrays.row_upper),
        ("variable bounds", arrays.lower_bounds, arrays.upper_bounds),
    ):
        if np.any(np.isnan(lower)) or np.any(np.isnan(upper)) or np.any(lower > upper):
            raise ValueError(
                f"{name} cannot contain NaN and each lower bound must not exceed upper"
            )
        _validate_finite_magnitude(lower, name)
        _validate_finite_magnitude(upper, name)


def _validated_linear_arrays(
    data: Mapping[str, Any], *, allow_empty: bool = False
) -> _LinearArrays:
    try:
        raw_objective = np.asarray(data["objective"])
    except (TypeError, ValueError) as exc:
        raise ValueError("objective must be a one-dimensional numeric array") from exc
    if raw_objective.ndim != 1:
        raise ValueError("objective must be a one-dimensional array of finite numbers")
    if raw_objective.size == 0 and not allow_empty:
        raise ValueError("objective must contain at least one coefficient")
    objective = _numeric_vector(raw_objective, "objective", raw_objective.size)

    matrix = _accurate_sparse_matrix(data["constraint_matrix"])
    if matrix.shape[1] != objective.size:
        raise ValueError("constraint_matrix must have one column per objective coefficient")
    magnitudes = np.abs(matrix.data)
    if not np.all(np.isfinite(magnitudes)):
        raise ValueError("constraint_matrix coefficients must be finite")
    if magnitudes.size and np.any(magnitudes <= LOCAL_SMALL_MATRIX_VALUE):
        raise ValueError(
            "constraint_matrix contains a nonzero coefficient at or below HiGHS' "
            f"supported matrix threshold ({LOCAL_SMALL_MATRIX_VALUE:g}); rescale the row"
        )
    if magnitudes.size and np.any(magnitudes >= LOCAL_LARGE_MATRIX_VALUE):
        raise ValueError(
            "constraint_matrix contains a coefficient at or above HiGHS' "
            f"supported matrix limit ({LOCAL_LARGE_MATRIX_VALUE:g}); rescale the row"
        )

    raw_names = data["variable_names"]
    if isinstance(raw_names, (str, bytes)):
        raise ValueError(
            "variable_names must contain one unique non-empty string per objective coefficient"
        )
    try:
        names = list(raw_names)
    except TypeError as exc:
        raise ValueError(
            "variable_names must contain one unique non-empty string per objective coefficient"
        ) from exc
    if (
        len(names) != objective.size
        or len(names) != len(set(names))
        or any(not isinstance(name, str) or not name for name in names)
    ):
        raise ValueError(
            "variable_names must contain one unique non-empty string per objective coefficient"
        )

    row_lower = _numeric_vector(data["row_lower"], "row_lower", matrix.shape[0])
    row_upper = _numeric_vector(data["row_upper"], "row_upper", matrix.shape[0])
    lower_bounds = _numeric_vector(data["lower_bounds"], "lower_bounds", objective.size)
    upper_bounds = _numeric_vector(data["upper_bounds"], "upper_bounds", objective.size)
    integrality = _validated_integrality(data, objective.size)
    raw_sense = data.get("sense", "min")
    if not isinstance(raw_sense, str) or raw_sense.upper() not in {"MIN", "MAX"}:
        raise ValueError("sense must be MIN or MAX")
    offset = data.get("objective_offset", 0.0)
    if not is_finite_real(offset):
        raise ValueError("objective_offset must be a finite number")
    arrays = _LinearArrays(
        objective=objective,
        matrix=matrix,
        names=names,
        row_lower=row_lower,
        row_upper=row_upper,
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
        integrality=integrality,
        sense=raw_sense.upper(),
        offset=float(offset),
    )
    _validate_linear_numeric_range(arrays)
    return arrays


class LocalProvider:
    """Free authenticated provider for QpiAI Opt local solving."""

    capabilities = ProviderCapabilities(
        name="qpiai-opt-local",
        mode="local",
        problem_classes=("LP", "MILP", "QP", "QUBO", "HUBO", "ISING"),
        exact=True,
        heuristic=True,
        requires_authentication=True,
        diagnostics=("status", "message", "solver work", "limits"),
        problem_capabilities={
            "LP": {"exact": True, "heuristic": False},
            "MILP": {"exact": True, "heuristic": False},
            "QP": {"exact": True, "heuristic": False},
            "QUBO": {"exact": True, "heuristic": True},
            "HUBO": {"exact": True, "heuristic": True},
            "ISING": {"exact": True, "heuristic": True},
        },
    )

    def __init__(self, client: Client | None = None) -> None:
        if client is None:
            raise AuthenticationError(
                "Local solving requires client=Client(api_key=...). "
                "Register for a free API key at https://opt.qpiai.tech/"
            )
        if not isinstance(client, Client):
            raise TypeError("local solving requires a Client")
        self.client = client

    def solve(
        self,
        problem: Any,
        *,
        problem_class: str,
        time_limit: float | None = None,
        mip_rel_gap: float | None = None,
        node_limit: int | None = None,
        num_reads: int | None = None,
        num_sweeps: int | None = None,
        seed: int | None = None,
        **options: Any,
    ) -> SolveResult:
        """Solve one validated problem through its class-specific local path."""

        try:
            from ._local_runtime import solve_local
        except ImportError as exc:
            raise ImportError(_LOCAL_RUNTIME_INSTALL_ERROR) from exc
        return solve_local(
            self.client.api_key,
            problem,
            problem_class=problem_class,
            time_limit=time_limit,
            mip_rel_gap=mip_rel_gap,
            node_limit=node_limit,
            num_reads=num_reads,
            num_sweeps=num_sweeps,
            seed=seed,
            **options,
        )
