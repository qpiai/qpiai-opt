"""Problem classification, inspection, provider routing, and blocking solve."""

from __future__ import annotations

import numbers
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .._numeric import is_finite_real
from ..opt_modeller import Model
from ._linear_file import read_linear_file_metadata
from .client import Client
from .exceptions import CapabilityError, LocalLimitExceeded
from .hubo import HUBOSolver
from .hubo.models import HUBOProblem, _validated_hubo_problem
from .ising import IsingProblem
from .local import (
    DEFAULT_POLYNOMIAL_NUM_READS,
    DEFAULT_POLYNOMIAL_NUM_SWEEPS,
    LOCAL_LIMITS,
    NATIVE_EXACT_MAX_VARIABLES,
    NATIVE_EXACT_MAX_WORK,
    LocalProvider,
    _annealing_work,
    _hubo_polynomial,
    _ising_polynomial,
    _linear_data,
    _local_stored_config,
    _model_polynomial,
    _polynomial_metrics,
    _qubo_polynomial,
    _validated_integrality,
    _validated_linear_arrays,
)
from .milp import MILPSolver
from .milp.models import MILPProblem, MILPSolverConfig
from .providers import ProviderCapabilities
from .qubo import QUBOSolver
from .qubo.models import QUBOProblem, _validated_qubo_problem
from .result_base import (
    _attach_solve_info,
    _attach_transformed_linear_info,
    _validated_result_update,
)
from .results import IsingSolveResult, SolveResult

if TYPE_CHECKING:
    from ..quantum import QuantumResult


def _platform_linear_input_error(problem_class: str) -> CapabilityError:
    return CapabilityError(
        problem_class,
        requested_mode="platform",
        available_providers={
            LocalProvider.capabilities.name: LocalProvider.capabilities.problem_classes,
            "qpiai-opt-platform": PlatformProvider.capabilities.problem_classes,
        },
        reason="QpiAI Platform accepts LP/MPS files, not a matrix dictionary",
        compatible_alternatives=(
            "solve the matrix locally",
            "construct a Model and use platform mode",
            "export an LP/MPS file and use platform mode",
        ),
    )


def _platform_qp_error() -> CapabilityError:
    return CapabilityError(
        "QP",
        requested_mode="platform",
        available_providers={
            LocalProvider.capabilities.name: LocalProvider.capabilities.problem_classes,
            PlatformProvider.capabilities.name: PlatformProvider.capabilities.problem_classes,
        },
        reason="continuous QP is available only in local mode",
        compatible_alternatives=("solve the QP locally", "export it through OMMX"),
    )


def _unsupported_platform_options(problem_class: str, options: set[str]) -> CapabilityError:
    names = ", ".join(sorted(options))
    return CapabilityError(
        problem_class,
        requested_mode="platform",
        available_providers={
            LocalProvider.capabilities.name: LocalProvider.capabilities.problem_classes,
            PlatformProvider.capabilities.name: PlatformProvider.capabilities.problem_classes,
        },
        reason=f"QpiAI Platform does not support these {problem_class} solve options: {names}",
        compatible_alternatives=(
            "remove the unsupported options",
            "solve locally with those options",
        ),
    )


def _empty_platform_qubo_error() -> CapabilityError:
    return CapabilityError(
        "QUBO",
        requested_mode="platform",
        available_providers={
            LocalProvider.capabilities.name: LocalProvider.capabilities.problem_classes,
            PlatformProvider.capabilities.name: PlatformProvider.capabilities.problem_classes,
        },
        reason="QpiAI Platform requires at least one nonzero QUBO coefficient",
        compatible_alternatives=("solve the zero-energy problem locally",),
    )


def _platform_options(
    problem_class: str,
    options: Mapping[str, Any],
    *,
    blocking: bool,
) -> dict[str, Any]:
    """Translate shared options and enforce one platform operation's contract."""

    translated = dict(options)
    if problem_class in {"LP", "MILP"}:
        aliases = {
            "time_limit": "time_limit",
            "mip_rel_gap": "mip_rel_gap",
            "node_limit": "mip_max_nodes",
        }
        if problem_class == "LP":
            aliases.pop("mip_rel_gap")
            aliases.pop("node_limit")
        supplied_aliases = aliases.keys() & translated.keys()
        if supplied_aliases:
            if "config" in translated:
                names = ", ".join(sorted(supplied_aliases))
                raise ValueError(
                    f"config cannot be combined with high-level linear options: {names}"
                )
            if "time_limit" in supplied_aliases and (
                not is_finite_real(translated["time_limit"]) or translated["time_limit"] <= 0
            ):
                raise ValueError("time_limit must be a finite positive number")
            if "mip_rel_gap" in supplied_aliases and (
                not is_finite_real(translated["mip_rel_gap"]) or translated["mip_rel_gap"] < 0
            ):
                raise ValueError("mip_rel_gap must be a finite non-negative number")
            if "node_limit" in supplied_aliases and (
                not isinstance(translated["node_limit"], int)
                or isinstance(translated["node_limit"], bool)
                or translated["node_limit"] <= 0
            ):
                raise ValueError("node_limit must be a positive integer")
            config_values = {aliases[name]: translated.pop(name) for name in supplied_aliases}
            translated["config"] = MILPSolverConfig.model_validate(config_values)

    allowed = {
        "QUBO": {
            "queue",
            "poll_interval",
            "timeout",
            "request_timeout",
            "name",
            "experiment_id",
            "experiment_name",
            "compute_resource_id",
        },
        "ISING": {
            "queue",
            "poll_interval",
            "timeout",
            "request_timeout",
            "name",
            "experiment_id",
            "experiment_name",
            "compute_resource_id",
        },
        "HUBO": {
            "queue",
            "poll_interval",
            "timeout",
            "request_timeout",
            "name",
            "experiment_id",
            "experiment_name",
            "compute_resource_id",
            "solver_config",
            "num_reads",
            "num_sweeps",
            "beta_min",
            "beta_max",
            "num_threads",
            "temperature_schedule",
            "seed",
        },
        "LP": {
            "config",
            "warm_start",
            "timeout",
            "poll_interval",
            "request_timeout",
            "name",
            "experiment_id",
            "experiment_name",
            "compute_resource_id",
            "workspace_id",
            "project_id",
        },
        "MILP": {
            "config",
            "warm_start",
            "timeout",
            "poll_interval",
            "request_timeout",
            "name",
            "experiment_id",
            "experiment_name",
            "compute_resource_id",
            "workspace_id",
            "project_id",
        },
    }
    if not blocking:
        allowed["QUBO"] -= {"queue", "poll_interval", "request_timeout"}
        allowed["ISING"] -= {"queue", "poll_interval", "request_timeout"}
        allowed["HUBO"] -= {"queue", "poll_interval", "request_timeout"}
        allowed["LP"] -= {"poll_interval", "request_timeout"}
        allowed["MILP"] -= {"poll_interval", "request_timeout"}
    unsupported = set(translated) - allowed[problem_class]
    if unsupported:
        raise _unsupported_platform_options(problem_class, unsupported)
    return translated


def _path_class(path: Path) -> str:
    return _path_report(path)[0]


def _path_report(path: Path) -> tuple[str, dict[str, int]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    lower = path.name.lower()
    if not lower.endswith((".lp", ".mps", ".lp.gz", ".mps.gz")):
        raise ValueError("Model file must end in .lp, .mps, .lp.gz, or .mps.gz")
    metadata = read_linear_file_metadata(path)
    return metadata.problem_class, {
        "file_bytes": metadata.file_bytes,
        "variables": metadata.variables,
        "constraints": metadata.constraints,
        "nonzeros": metadata.nonzeros,
    }


def classify(problem: Any) -> str:
    """Return a supported mathematical class without solving."""

    if isinstance(problem, Model):
        detected = problem.validate()
        return "MILP" if detected == "MIP" else detected
    if isinstance(problem, QUBOProblem):
        return "QUBO"
    if isinstance(problem, HUBOProblem):
        return "HUBO"
    if isinstance(problem, IsingProblem):
        return "ISING"
    if isinstance(problem, MILPProblem):
        return problem.problem_type
    try:
        from qpiai_quantum.applications.matter.models import QubitHamiltonian
    except ImportError:
        QubitHamiltonian = ()
    if isinstance(problem, QubitHamiltonian):
        return "QUBIT_HAMILTONIAN"
    if isinstance(problem, (str, Path)):
        return _path_class(Path(problem))
    if isinstance(problem, Mapping):
        declarations = []
        for key in ("problem_type", "method"):
            if key in problem:
                value = problem[key]
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a problem-class string")
                declarations.append("MILP" if value.upper() == "MIP" else value.upper())
        if len(set(declarations)) > 1:
            raise ValueError("problem_type and method declare conflicting problem classes")
        marker = declarations[0] if declarations else ""
        allowed_markers = {"LP", "MILP", "QUBO", "HUBO", "ISING"}
        if declarations and marker not in allowed_markers:
            raise ValueError("problem_type and method must be LP, MILP, MIP, QUBO, HUBO, or ISING")
        if marker in {"LP", "MILP", "MIP", "QUBO", "HUBO", "ISING"}:
            if marker in {"LP", "MILP"} and ("objective" in problem or "c" in problem):
                data, _ = _linear_data(problem)
                actual = (
                    "MILP"
                    if np.any(_validated_integrality(data, len(data["objective"])) != 0)
                    else "LP"
                )
                if marker != actual:
                    raise ValueError(
                        f"declared problem class {marker} conflicts with integrality-derived {actual}"
                    )
            return marker
        if "qubo_dict" in problem:
            return "QUBO"
        if "polynomial_dict" in problem:
            return "HUBO"
        if {"linear", "quadratic"} <= problem.keys():
            return "ISING"
        if "objective" in problem or "c" in problem:
            data, _ = _linear_data(problem)
            integrality = _validated_integrality(data, len(data["objective"]))
            return "MILP" if integrality.size and np.any(integrality != 0) else "LP"
        if problem and all(isinstance(key, tuple) for key in problem):
            degree = max(len(key) for key in problem)
            if degree > 2:
                return "HUBO"
            if all(
                len(key) == 2
                and all(
                    isinstance(index, numbers.Integral) and not isinstance(index, bool)
                    for index in key
                )
                for key in problem
            ):
                return "QUBO"
            raise ValueError(
                "A tuple-key polynomial of degree two or less is ambiguous. Use QUBOProblem "
                "for indexed QUBO data or HUBOProblem for labelled polynomial data."
            )
    raise TypeError(
        "Cannot classify the problem. Use Model, QUBOProblem, HUBOProblem, IsingProblem, MILPProblem, "
        "QubitHamiltonian, an LP/MPS path, or a documented matrix dictionary."
    )


def _normalize(problem: Any, problem_class: str) -> Any:
    if isinstance(problem, QUBOProblem):
        return _validated_qubo_problem(problem)
    if isinstance(problem, HUBOProblem):
        return _validated_hubo_problem(problem)
    if isinstance(problem, IsingProblem):
        validated = IsingProblem.model_validate(problem.model_dump())
        validated._source_model = problem._source_model
        return validated
    if isinstance(problem, MILPProblem):
        return MILPProblem(
            problem_type=problem.problem_type,
            model=problem.model,
            model_path=problem.model_path,
            config=MILPSolverConfig.model_validate(problem.config.model_dump(exclude_unset=True))
            if problem.config is not None
            else None,
        )
    if not isinstance(problem, Mapping):
        return problem
    data = dict(problem)
    data.pop("method", None)
    if problem_class == "QUBO":
        if "qubo_dict" not in data:
            data.pop("problem_type", None)
            data = {"qubo_dict": data, "matrix_format": "upper"}
        qubo = data["qubo_dict"]
        if not isinstance(qubo, dict):
            raise ValueError("qubo_dict must be a dictionary")
        if "num_variables" not in data:
            if not qubo:
                raise ValueError("num_variables is required for an empty QUBO dictionary")
            if any(
                not isinstance(key, tuple)
                or len(key) != 2
                or any(
                    not isinstance(index, numbers.Integral) or isinstance(index, bool) or index < 0
                    for index in key
                )
                for key in qubo
            ):
                raise ValueError(
                    "QUBO keys must be pairs of non-negative integer indices before "
                    "num_variables can be inferred"
                )
            data["num_variables"] = 1 + max(index for key in qubo for index in key)
        return QUBOProblem.model_validate(data)
    if problem_class == "HUBO":
        if "polynomial_dict" not in data:
            data.pop("problem_type", None)
            data = {"polynomial_dict": data}
        if not isinstance(data["polynomial_dict"], dict):
            raise ValueError("polynomial_dict must be a dictionary")
        if "num_variables" not in data:
            polynomial = data["polynomial_dict"]
            if any(
                not isinstance(term, tuple)
                or any(not isinstance(label, str) or not label for label in term)
                for term in polynomial
            ):
                raise ValueError(
                    "HUBO terms must be tuples of non-empty string labels before "
                    "num_variables can be inferred"
                )
            data["num_variables"] = len({label for term in polynomial for label in term})
        return HUBOProblem.model_validate(data)
    if problem_class == "ISING":
        data.pop("problem_type", None)
        for field in ("linear", "quadratic"):
            if field in data and not isinstance(data[field], dict):
                raise ValueError(f"{field} must be a dictionary")
        if "num_variables" not in data:
            indices = list(data.get("linear", {}))
            for pair in data.get("quadratic", {}):
                if not isinstance(pair, tuple) or len(pair) != 2:
                    raise ValueError("Ising quadratic keys must be index pairs")
                indices.extend(pair)
            if any(
                not isinstance(index, numbers.Integral) or isinstance(index, bool) or index < 0
                for index in indices
            ):
                raise ValueError("Ising indices must be non-negative integers")
            data["num_variables"] = max(indices, default=-1) + 1
        return IsingProblem.model_validate(data)
    return data


class PlatformProvider:
    """Authenticated provider for QpiAI Platform problem classes."""

    capabilities = ProviderCapabilities(
        name="qpiai-opt-platform",
        mode="platform",
        problem_classes=("LP", "MILP", "QUBO", "HUBO", "ISING"),
        exact=False,
        heuristic=True,
        requires_authentication=True,
        diagnostics=("job_id", "problem_id", "status", "platform response"),
        problem_capabilities={
            "LP": {"exact": True, "heuristic": False},
            "MILP": {"exact": True, "heuristic": False},
            "QUBO": {"exact": False, "heuristic": True},
            "HUBO": {"exact": False, "heuristic": True},
            "ISING": {"exact": False, "heuristic": True},
        },
    )

    def __init__(self, client: Client) -> None:
        if not isinstance(client, Client):
            raise TypeError("platform solving requires a Client")
        self.client = client

    def solve(self, problem: Any, *, problem_class: str, **options: Any) -> SolveResult:
        """Solve one classified problem through its platform solver."""

        if problem_class == "QP":
            raise _platform_qp_error()
        if isinstance(problem, MILPProblem):
            problem, options = problem._execution_input(options, mode="platform")
        options = _platform_options(problem_class, options, blocking=True)
        ising_origin = None
        source_model = problem if isinstance(problem, Model) else None
        if problem_class == "ISING":
            if isinstance(problem, Model):
                ising = problem.compile(target="ISING")
            else:
                ising = problem
            ising_origin = ising
            canonical_ising = ising._as_minimization()
            from ..opt_modeller import ising_to_qubo

            converted = ising_to_qubo(
                canonical_ising.linear,
                canonical_ising.quadratic,
                constant=canonical_ising.offset,
                matrix_format="upper",
                num_variables=ising.num_variables,
                variable_names=ising.variable_names or None,
                original_sense=ising.sense,
            )
            problem = QUBOProblem(
                num_variables=ising.num_variables,
                qubo_dict=converted.qubo,
                matrix_format="upper",
                offset=converted.offset,
                variable_names=ising.variable_names or None,
                sense="MIN",
            )
            problem_class = "QUBO"

        if problem_class == "QUBO":
            if isinstance(problem, Model):
                problem = QUBOProblem.from_model(problem)
            if not problem.qubo_dict:
                raise _empty_platform_qubo_error()
            platform_result = QUBOSolver(client=self.client).solve(problem, **options)
            if ising_origin is not None:
                binary_values = platform_result.solution
                spins = [2 * value - 1 for value in binary_values]
                variable_assignment = ising_origin.decode(spins)
                objective = float(ising_origin.source_objective(spins))
                result = IsingSolveResult(
                    execution_mode="platform",
                    num_variables=ising_origin.num_variables,
                    objective=objective,
                    solver_time_s=platform_result.solver_time_s,
                    compute_time_s=platform_result.compute_time_s,
                    solution=[2 * value - 1 for value in binary_values],
                    variable_assignment=variable_assignment,
                    job_id=platform_result.job_id,
                    problem_id=platform_result.problem_id,
                )
                source_feasible = (
                    ising_origin._source_model.is_feasible(variable_assignment)
                    if ising_origin._source_model is not None
                    else True
                )
                return _attach_solve_info(
                    result,
                    provider="qpiai-opt-platform",
                    solver="QpiAI Opt QUBO Solver",
                    method="QUBO",
                    termination="completed",
                    has_usable_solution=source_feasible,
                    solution_feasible=source_feasible,
                    optimality_proven=False,
                    guarantee="best_found" if source_feasible else "none",
                    transformed=True,
                    settings=platform_result.info().settings,
                )
            return _validated_result_update(platform_result, problem_class=problem_class)
        elif problem_class == "HUBO":
            if isinstance(problem, Model):
                problem = HUBOProblem.from_model(problem)
            platform_result = HUBOSolver(client=self.client).solve(problem, **options)
            return platform_result
        elif problem_class in {"LP", "MILP"}:
            if isinstance(problem, Model):
                with tempfile.TemporaryDirectory(prefix="qpiai-opt-") as directory:
                    path = Path(directory) / "model.lp"
                    problem.to_lp_file(path)
                    platform_result = MILPSolver(client=self.client).solve(str(path), **options)
            elif isinstance(problem, (str, Path)):
                platform_result = MILPSolver(client=self.client).solve(str(problem), **options)
            else:
                raise _platform_linear_input_error(problem_class)
            update = {"problem_class": problem_class}
            if source_model is not None:
                update["sense"] = source_model.sense
                if platform_result.solution is not None:
                    update["objective"] = float(
                        source_model.evaluate_objective(platform_result.solution)
                    )
            decoded_result = _validated_result_update(platform_result, **update)
            if source_model is None:
                return decoded_result
            source_feasible = (
                source_model.is_feasible(platform_result.solution)
                if platform_result.solution is not None
                else None
            )
            return _attach_transformed_linear_info(
                decoded_result,
                source_feasible=source_feasible,
            )
        else:  # pragma: no cover - guarded by classify
            raise CapabilityError(
                problem_class,
                requested_mode="platform",
                available_providers={"qpiai-opt-platform": self.capabilities.problem_classes},
            )


def provider_capabilities(*, include_platform: bool = True) -> list[dict[str, Any]]:
    """Return machine-readable local and optional platform capabilities."""

    if not isinstance(include_platform, bool):
        raise TypeError("include_platform must be a boolean")
    result = [LocalProvider.capabilities.as_dict()]
    if include_platform:
        result.append(PlatformProvider.capabilities.as_dict())
    return result


def inspect(problem: Any, *, algorithm: Any = None) -> dict[str, Any]:
    """Return deterministic classification, representation, and validated structural metrics."""

    if algorithm is not None and classify(problem) == "QUBIT_HAMILTONIAN":
        from ..quantum.core import algorithm_report

        quantum = algorithm_report(problem, algorithm)
        return {
            "problem_class": "QUBIT_HAMILTONIAN",
            "representation": type(problem).__name__,
            "metrics": {
                "variables": quantum.get("qubits", 0),
                "terms": quantum.get("hamiltonian_terms", 0),
                "max_locality": quantum.get("max_locality", 0),
            },
            "valid": True,
            "quantum": quantum,
        }

    metrics: dict[str, int] = {}
    if isinstance(problem, (str, Path)):
        normalized = problem
        problem_class, metrics = _path_report(Path(problem))
    else:
        problem_class = classify(problem)
        normalized = _normalize(problem, problem_class)

    representation = type(normalized).__name__
    if isinstance(normalized, MILPProblem):
        normalized = normalized.model if normalized.model is not None else normalized.model_path
        if isinstance(normalized, Path):
            problem_class, metrics = _path_report(normalized)

    if problem_class == "QUBIT_HAMILTONIAN":
        from ..quantum._shared import _n_qubits, _terms

        terms = _terms(normalized)
        metrics = {
            "variables": _n_qubits(terms, normalized),
            "terms": len(terms),
            "max_locality": max((len(ops) for ops, _ in terms), default=0),
        }
        report = {
            "problem_class": problem_class,
            "representation": representation,
            "metrics": metrics,
            "valid": True,
        }
        if algorithm is not None:
            from ..quantum.core import algorithm_report

            report["quantum"] = algorithm_report(problem, algorithm)
        return report

    if not isinstance(normalized, (str, Path)) and problem_class in {"LP", "MILP"}:
        data, _ = _linear_data(normalized)
        arrays = _validated_linear_arrays(data, allow_empty=isinstance(normalized, Model))
        metrics = {
            "variables": int(arrays.objective.size),
            "constraints": int(arrays.matrix.shape[0]),
            "nonzeros": int(arrays.matrix.nnz),
        }
    elif not isinstance(normalized, (str, Path)) and problem_class == "QP":
        data = normalized.to_qp_matrix()
        metrics = {
            "variables": len(data["variable_names"]),
            "constraints": int(data["constraint_matrix"].shape[0]),
            "nonzeros": int(data["constraint_matrix"].nnz),
            "hessian_nonzeros": int(data["objective_hessian"].nnz),
        }
    elif not isinstance(normalized, (str, Path)):
        declared_over_limit = (
            isinstance(normalized, (QUBOProblem, IsingProblem))
            and normalized.num_variables > LOCAL_LIMITS[problem_class]["variables"]
        )
        if isinstance(normalized, Model):
            polynomial = _model_polynomial(normalized, problem_class)
        elif isinstance(normalized, QUBOProblem):
            polynomial = _qubo_polynomial(
                normalized,
                include_isolated=not declared_over_limit,
            )
        elif isinstance(normalized, HUBOProblem):
            polynomial = _hubo_polynomial(normalized)
        elif isinstance(normalized, IsingProblem):
            polynomial = _ising_polynomial(
                normalized,
                include_isolated=not declared_over_limit,
            )
        else:  # pragma: no cover - normalized representations are exhaustive
            raise TypeError(type(normalized).__name__)
        metrics = _polynomial_metrics(polynomial)
        if declared_over_limit:
            metrics["variables"] = normalized.num_variables
        stored_config = _local_stored_config(normalized)
        use_exact = (
            metrics["active_variables"] <= NATIVE_EXACT_MAX_VARIABLES
            and metrics["exact_enumeration_work"] <= NATIVE_EXACT_MAX_WORK
        )
        if not use_exact:
            metrics["annealing_work"] = _annealing_work(
                polynomial,
                stored_config.get("num_reads", DEFAULT_POLYNOMIAL_NUM_READS),
                stored_config.get("num_sweeps", DEFAULT_POLYNOMIAL_NUM_SWEEPS),
            )
    report = {
        "problem_class": problem_class,
        "representation": representation,
        "metrics": metrics,
        "valid": True,
    }
    if algorithm is not None:
        from ..quantum.core import algorithm_report

        report["quantum"] = algorithm_report(normalized, algorithm)
    return report


def solve(
    problem: Any,
    *,
    mode: str = "local",
    client: Client | None = None,
    allow_platform: bool = False,
    **options: Any,
) -> SolveResult | QuantumResult:
    """Classify and solve a problem locally by default.

    Local and auto execution without an algorithm require ``client=Client(...)`` and
    verify its credential online before each solve.
    ``mode='auto'`` falls back to the paid platform only when
    ``allow_platform=True`` is supplied explicitly.
    """

    compute_started_ns = time.perf_counter_ns()
    if not isinstance(mode, str) or mode.lower() not in {"local", "platform", "auto"}:
        raise ValueError("mode must be 'local', 'platform', or 'auto'")
    mode = mode.lower()
    if not isinstance(allow_platform, bool):
        raise TypeError("allow_platform must be a boolean")
    algorithm_supplied = "algorithm" in options
    execution_supplied = "execution" in options
    algorithm = options.pop("algorithm", None)
    execution = options.pop("execution", None)
    if algorithm_supplied:
        if algorithm is None:
            raise TypeError("algorithm must be a gate-based optimization algorithm configuration, not None")
        if allow_platform:
            raise ValueError(
                "allow_platform is not valid for gate-based optimization algorithms; choose mode='local' or mode='platform' explicitly"
            )
        from ..quantum.core import run_algorithm

        if options:
            raise ValueError(
                "gate-based optimization call contains unsupported options: " + ", ".join(sorted(options))
            )
        quantum_problem_class = classify(problem)
        normalized_quantum_problem = (
            problem
            if isinstance(problem, Model) or quantum_problem_class == "QUBIT_HAMILTONIAN"
            else _normalize(problem, quantum_problem_class)
        )
        result = run_algorithm(
            normalized_quantum_problem,
            algorithm,
            mode=mode,
            execution=execution,
            client=client,
        )
        result.compute_time_s = (time.perf_counter_ns() - compute_started_ns) / 1_000_000_000.0
        return result
    if execution_supplied:
        raise ValueError("execution= is valid only when algorithm= selects a quantum algorithm")
    problem_class = classify(problem)
    normalized = _normalize(problem, problem_class)
    if problem_class == "QUBIT_HAMILTONIAN":
        raise CapabilityError(
            problem_class,
            requested_mode=mode,
            available_providers={"qpiai-quantum": ("VQE", "SamplingVQE", "QAOA-family")},
            reason="a QubitHamiltonian requires an explicit quantum algorithm= configuration",
        )
    if mode == "local":
        result = LocalProvider(client).solve(normalized, problem_class=problem_class, **options)
        result.compute_time_s = (time.perf_counter_ns() - compute_started_ns) / 1_000_000_000.0
        return result
    if mode == "platform":
        if client is None:
            raise ValueError("mode='platform' requires client=Client(...)")
        return PlatformProvider(client).solve(normalized, problem_class=problem_class, **options)
    try:
        result = LocalProvider(client).solve(normalized, problem_class=problem_class, **options)
        result.compute_time_s = (time.perf_counter_ns() - compute_started_ns) / 1_000_000_000.0
        return result
    except LocalLimitExceeded:
        if not allow_platform:
            raise
        if client is None:
            raise ValueError("auto platform fallback requires client=Client(...)") from None
        return PlatformProvider(client).solve(normalized, problem_class=problem_class, **options)
