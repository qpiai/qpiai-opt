"""Validated Python operations for inspection, conversion, and job automation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .._numeric import DEFAULT_FEASIBILITY_TOLERANCE
from ..opt_modeller import (
    Model,
    from_ir,
    hubo_to_ising,
    hubo_to_qubo,
    qubo_to_ising,
    to_ir,
)
from ..opt_modeller.utils.exceptions import QuboModellerError
from ._linear_file import read_linear_file_metadata
from .client import Client
from .exceptions import CapabilityError
from .hubo import HUBOSolver
from .hubo.models import HUBOProblem
from .ising import IsingProblem
from .milp import MILPSolver
from .milp.models import MILPProblem, MILPSolverConfig, WarmStart
from .platform_models import (
    CreditQuota,
    CreditResourceUsage,
    CreditUsage,
    CreditUsageByResource,
    CreditUsageJob,
    Job,
    JobSubmission,
)
from .qubo import QUBOSolver
from .qubo.models import QUBOProblem
from .result_base import (
    SolveInfo,
    _attach_solve_info,
    _validated_result_update,
)
from .results import IsingSolveResult, SolveResult
from .routing import (
    _normalize,
    _platform_linear_input_error,
    _platform_options,
    _platform_qp_error,
    classify,
)


class _InspectRequest(BaseModel):
    problem_ir: dict[str, Any]
    model_config = ConfigDict(extra="forbid")


class _SolveRequest(_InspectRequest):
    mode: Literal["local", "platform", "auto"] = "local"
    allow_platform: bool = False
    time_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    mip_rel_gap: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    node_limit: int | None = Field(default=None, gt=0)
    num_reads: int | None = Field(default=None, gt=0)
    num_sweeps: int | None = Field(default=None, gt=0)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class _SubmitRequest(_InspectRequest):
    pass


class _JobRequest(BaseModel):
    job_id: str = Field(min_length=1)
    model_config = ConfigDict(extra="forbid")


class _FeasibilityRequest(BaseModel):
    model_ir: dict[str, Any]
    values: dict[str, float]
    tolerance: float = Field(
        default=DEFAULT_FEASIBILITY_TOLERANCE,
        ge=0,
        allow_inf_nan=False,
    )
    model_config = ConfigDict(extra="forbid")


def convert(problem: Any, target: str, **options: Any) -> Any:
    """Convert a supported representation without dropping unsupported semantics.

    Model exports return dictionaries/triplets as documented. Explicit native
    cross-conversions to QUBO retain their named conversion records; ISING
    exports return the same source-coordinate dictionary as to_ising_dict().
    """

    if not isinstance(target, str):
        raise TypeError("target must be a string")
    target = target.upper()
    if isinstance(problem, dict) and problem.get("ir_version"):
        problem = from_ir(problem)
    if target == "IR":
        if options:
            raise ValueError("IR conversion does not accept options: " + ", ".join(sorted(options)))
        return to_ir(problem)
    source = classify(problem)
    problem = _normalize(problem, source)
    if isinstance(problem, MILPProblem):
        problem = problem._as_model()
    if isinstance(problem, Model):
        if target == "QUBIT_HAMILTONIAN":
            from ..quantum._shared import _hubo_to_qubit_hamiltonian, convert_to_qubit_hamiltonian

            if problem.validate() == "ISING":
                return convert_to_qubit_hamiltonian(problem.compile(target="ISING", **options))
            return _hubo_to_qubit_hamiltonian(problem.compile(target="HUBO", **options))
        if target == "OMMX":
            if options:
                raise ValueError("OMMX conversion does not accept options")
            return problem.to_ommx()
        if target == "QUBO":
            return problem.to_qubo_dict(**options)
        if target == "HUBO":
            return problem.to_hubo_dict(**options)
        if target == "ISING":
            return problem.to_ising_dict(**options)
        if target in {"LP", "MPS"}:
            if options:
                raise ValueError(f"{target} string conversion does not accept options")
            return problem.to_lp_string() if target == "LP" else problem.to_mps_string()
    if target == "QUBIT_HAMILTONIAN":
        from ..quantum._shared import convert_to_qubit_hamiltonian

        if isinstance(problem, HUBOProblem):
            if options:
                raise ValueError("HUBOProblem to QUBIT_HAMILTONIAN does not accept options")
            from ..quantum._shared import _hubo_to_qubit_hamiltonian

            return _hubo_to_qubit_hamiltonian(problem)
        if options:
            raise ValueError(
                "QUBIT_HAMILTONIAN conversion does not accept options: "
                + ", ".join(sorted(options))
            )
        if isinstance(problem, (QUBOProblem, IsingProblem)):
            return convert_to_qubit_hamiltonian(problem)
    if source == "QUBIT_HAMILTONIAN" and target in {"ISING", "QUBO"}:
        from ..quantum._shared import convert_from_qubit_hamiltonian

        allowed = {"matrix_format"} if target == "QUBO" else set()
        unknown = set(options) - allowed
        if unknown:
            raise ValueError(
                f"QubitHamiltonian to {target} contains unsupported options: "
                + ", ".join(sorted(unknown))
            )
        converted = convert_from_qubit_hamiltonian(problem)
        if target == "ISING":
            return IsingProblem._from_minimization(converted).model_dump()
        return converted.to_qubo(**options)
    if isinstance(problem, QUBOProblem) and target == "ISING":
        if options:
            raise ValueError(
                "QUBOProblem to ISING does not accept options: " + ", ".join(sorted(options))
            )
        sign = -1.0 if problem.sense == "MAX" else 1.0
        converted = qubo_to_ising(
            {key: sign * value for key, value in problem.qubo_dict.items()},
            num_variables=problem.num_variables,
            constant=sign * problem.offset,
            variable_names=problem.variable_names,
        ).with_original_sense(problem.sense)
        return IsingProblem._from_minimization(converted).model_dump()
    if isinstance(problem, HUBOProblem) and target in {"QUBO", "ISING"}:
        unknown = set(options) - {"strength", "auxiliary_prefix", "matrix_format"}
        if unknown:
            raise ValueError(
                f"HUBOProblem to {target} contains unsupported options: "
                + ", ".join(sorted(unknown))
            )
        if target == "QUBO":
            return hubo_to_qubo(
                problem.polynomial_dict,
                sense=problem.sense,
                variable_names=problem.variable_names,
                **options,
            )
        converted = hubo_to_ising(
            problem.polynomial_dict,
            sense=problem.sense,
            variable_names=problem.variable_names,
            **options,
        )
        return IsingProblem._from_minimization(converted).model_dump()
    if isinstance(problem, IsingProblem):
        if target == "ISING":
            if options:
                raise ValueError("ISING conversion does not accept options")
            return problem.model_dump()
        if target == "QUBO":
            unknown = set(options) - {"matrix_format"}
            if unknown:
                raise ValueError(
                    "IsingProblem to QUBO contains unsupported options: "
                    + ", ".join(sorted(unknown))
                )
            return problem._as_minimization().to_qubo(**options)
    raise ValueError(f"Conversion from {source} to {target} is not supported")


def feasibility(
    model: Model,
    values: dict[str, float],
    *,
    tolerance: float = DEFAULT_FEASIBILITY_TOLERANCE,
) -> dict:
    """Evaluate a complete, name-keyed assignment against a source model."""

    if not isinstance(model, Model):
        raise TypeError("feasibility requires a Model")
    if not isinstance(values, dict):
        raise TypeError("values must be a dictionary keyed by source variable name")
    model_variables = model.variables()
    source_names = {
        variable.name
        for variable in model_variables
        if not getattr(variable, "_is_auxiliary", False)
    }
    missing = sorted(source_names - values.keys())
    if missing:
        raise ValueError("values are missing source variables: " + ", ".join(map(repr, missing)))
    accepted_names = {variable.name for variable in model_variables}
    unknown = [key for key in values if not isinstance(key, str) or key not in accepted_names]
    if unknown:
        # Linear reformulations can add deterministic columns to a returned solution.
        try:
            accepted_names.update(model.to_lp_matrix(for_milp=True)["variable_names"])
        except QuboModellerError:
            pass
        unknown = [key for key in values if not isinstance(key, str) or key not in accepted_names]
    if unknown:
        raise ValueError(
            "values contain unknown source variables: " + ", ".join(sorted(map(repr, unknown)))
        )
    violations = model.constraint_violations(values, tolerance=tolerance)
    return {
        "feasible": all(value == 0.0 for value in violations.values()),
        "objective": float(model.evaluate_objective(values)),
        "violations": violations,
        "tolerance": tolerance,
    }


def submit(
    problem: Any,
    *,
    client: Client,
    **options: Any,
) -> dict[str, Any]:
    """Explicitly submit a paid platform job once; no automatic retry is performed."""

    if not isinstance(client, Client):
        raise TypeError("submit requires client=Client(...)")
    problem_class = classify(problem)
    normalized = _normalize(problem, problem_class)
    if problem_class == "QP":
        raise _platform_qp_error()
    if problem_class == "QUBIT_HAMILTONIAN":
        raise CapabilityError(
            problem_class,
            requested_mode="platform",
            available_providers={},
            reason="nonblocking optimization job submission does not support quantum algorithms",
            compatible_alternatives=(
                "solve(problem, algorithm=..., execution=..., mode='platform')",
            ),
        )
    if isinstance(normalized, MILPProblem):
        normalized, options = normalized._execution_input(options, mode="platform")
    options = _platform_options(problem_class, options, blocking=False)
    if problem_class == "QUBO":
        if isinstance(normalized, Model):
            normalized = QUBOProblem.from_model(normalized)
        response = QUBOSolver(client=client).submit(normalized, **options)
    elif problem_class == "HUBO":
        if isinstance(normalized, Model):
            normalized = HUBOProblem.from_model(normalized)
        response = HUBOSolver(client=client).submit(normalized, **options)
    elif problem_class == "ISING":
        ising = normalized.compile(target="ISING") if isinstance(normalized, Model) else normalized
        converted = ising._as_minimization().to_qubo(matrix_format="upper")
        qubo = QUBOProblem(
            num_variables=converted.num_variables,
            qubo_dict=converted.qubo,
            matrix_format="upper",
            offset=converted.offset,
            variable_names=converted.variable_names or None,
        )
        response = QUBOSolver(client=client).submit(qubo, **options)
    elif problem_class in {"LP", "MILP"}:
        if isinstance(normalized, Model):
            with tempfile.TemporaryDirectory(prefix="qpiai-opt-") as directory:
                path = Path(directory) / "model.lp"
                normalized.to_lp_file(path)
                response = MILPSolver(client=client).submit(str(path), **options)
        elif isinstance(normalized, (str, Path)):
            response = MILPSolver(client=client).submit(str(normalized), **options)
        else:
            raise _platform_linear_input_error(problem_class)
    else:  # pragma: no cover - classify is exhaustive
        raise ValueError(f"Unsupported platform problem class {problem_class}")
    data = response.model_dump(mode="json")
    data.update({"detected_class": problem_class, "mode": "platform"})
    return data


def status(client: Client, job_id: str) -> dict[str, Any]:
    """Return normalized and raw lifecycle state for a platform job."""

    if not isinstance(client, Client):
        raise TypeError("status requires client=Client(...)")
    job = client.get_job(job_id)
    return {
        "job_id": job.id,
        "status": job.normalized_status.value,
        "raw_status": job.status,
        "error": job.error,
    }


def result(client: Client, job_id: str, *, problem: Any = None) -> Any:
    """Return a completed platform result, optionally decoded through its source problem."""

    if not isinstance(client, Client):
        raise TypeError("result requires client=Client(...)")
    job = client.get_job(job_id)
    if job.normalized_status.value != "completed":
        raise RuntimeError(f"Job {job.id} is {job.normalized_status.value}, not completed")
    if problem is not None:
        problem_class = classify(problem)
        normalized = _normalize(problem, problem_class)
        if isinstance(normalized, MILPProblem):
            normalized = normalized.model if normalized.model is not None else normalized.model_path
        source_model = normalized if isinstance(normalized, Model) else None
        if problem_class == "QUBO":
            qubo = QUBOProblem.from_model(normalized) if source_model is not None else normalized
            return QUBOSolver(client=client)._parse_result(job, problem=qubo)
        if problem_class == "HUBO":
            hubo = normalized
            if source_model is not None:
                hubo = HUBOProblem.from_model(source_model)
            typed = HUBOSolver(client=client)._parse_result(job, problem=hubo)
            return typed
        if problem_class == "ISING":
            ising = normalized.compile(target="ISING") if source_model is not None else normalized
            converted = ising._as_minimization().to_qubo(matrix_format="upper")
            qubo = QUBOProblem(
                num_variables=converted.num_variables,
                qubo_dict=converted.qubo,
                matrix_format="upper",
                offset=converted.offset,
                variable_names=converted.variable_names or None,
            )
            typed = QUBOSolver(client=client)._parse_result(job, problem=qubo)
            spins = [2 * value - 1 for value in typed.solution]
            assignment = ising.decode(spins)
            objective = ising.source_objective(spins)
            if source_model is not None:
                source_variables = source_model.variables()
                if source_variables and all(
                    variable.var_type == "SPIN" for variable in source_variables
                ):
                    assignment = {
                        variable.name: assignment[variable.name] for variable in source_variables
                    }
                else:  # pragma: no cover - an Ising-class Model contains only spins
                    assignment = source_model.decode_qubo_sample(typed.solution)
                objective = source_model.evaluate_objective(assignment)
            solve_result = IsingSolveResult(
                execution_mode="platform",
                num_variables=ising.num_variables,
                objective=float(objective),
                solver_time_s=typed.solver_time_s,
                compute_time_s=typed.compute_time_s,
                solution=spins,
                variable_assignment=assignment,
                job_id=typed.job_id,
                problem_id=typed.problem_id,
            )
            source_feasible = (
                source_model.is_feasible(assignment)
                if source_model is not None
                else ising._source_model.is_feasible(assignment)
                if ising._source_model is not None
                else True
            )
            return _attach_solve_info(
                solve_result,
                provider="qpiai-opt-platform",
                solver="QpiAI Opt QUBO Solver",
                method="QUBO",
                termination="completed",
                has_usable_solution=source_feasible,
                solution_feasible=source_feasible,
                optimality_proven=False,
                guarantee="best_found" if source_feasible else "none",
                transformed=True,
                settings=typed.info().settings,
            )
        if problem_class == "QP":
            raise _platform_qp_error()
        source_metadata = (
            read_linear_file_metadata(Path(normalized))
            if isinstance(normalized, (str, Path))
            else None
        )
        if source_model is not None:
            return MILPSolver(client=client)._parse_result(job, source_model=source_model)
        typed = MILPSolver(client=client)._parse_result(job, source_metadata=source_metadata)
        return _validated_result_update(typed, problem_class=problem_class)
    return client.extract_result(job)


def cancel(client: Client, job_id: str) -> dict[str, Any]:
    """Request cancellation of a platform job."""

    if not isinstance(client, Client):
        raise TypeError("cancel requires client=Client(...)")
    return client.cancel_job(job_id)


def schemas() -> dict[str, Any]:
    """Return stable JSON schemas for automation-facing inputs and outputs."""

    from ..quantum.types import (
        CVaR,
        LogicalCircuitInfo,
        QuantumDiagnostics,
        QuantumResult,
        QuantumRun,
        QuantumSample,
        QuantumSolveInfo,
        XYMixer,
    )
    from ..quantum.types import (
        WarmStart as QuantumWarmStart,
    )
    from .hubo.models import SolveResult as HUBOSolveResult
    from .milp.models import SolveResult as MILPSolveResult
    from .qubo.models import SolveResult as QUBOSolveResult
    from .results import QPSolveResult

    return {
        "inspect_request": _InspectRequest.model_json_schema(),
        "solve_request": _SolveRequest.model_json_schema(),
        "submit_request": _SubmitRequest.model_json_schema(),
        "job_request": _JobRequest.model_json_schema(),
        "feasibility_request": _FeasibilityRequest.model_json_schema(),
        "solve_result": TypeAdapter(SolveResult | QuantumResult).json_schema(),
        "solve_info": SolveInfo.model_json_schema(),
        "job": Job.model_json_schema(),
        "job_submission": JobSubmission.model_json_schema(),
        "credit_usage": CreditUsage.model_json_schema(),
        "credit_usage_by_resource": CreditUsageByResource.model_json_schema(),
        "credit_usage_job": CreditUsageJob.model_json_schema(),
        "credit_resource_usage": CreditResourceUsage.model_json_schema(),
        "credit_quota": CreditQuota.model_json_schema(),
        "qubo_problem": QUBOProblem.model_json_schema(),
        "hubo_problem": HUBOProblem.model_json_schema(),
        "ising_problem": IsingProblem.model_json_schema(),
        "milp_problem": MILPProblem.model_json_schema(),
        "milp_solver_config": MILPSolverConfig.model_json_schema(),
        "milp_warm_start": WarmStart.model_json_schema(),
        "qubo_result": QUBOSolveResult.model_json_schema(),
        "hubo_result": HUBOSolveResult.model_json_schema(),
        "ising_result": IsingSolveResult.model_json_schema(),
        "qp_result": QPSolveResult.model_json_schema(),
        "milp_result": MILPSolveResult.model_json_schema(),
        "quantum_run": QuantumRun.model_json_schema(),
        "quantum_cvar": CVaR.model_json_schema(),
        "quantum_warm_start": QuantumWarmStart.model_json_schema(),
        "quantum_xy_mixer": XYMixer.model_json_schema(),
        "quantum_sample": QuantumSample.model_json_schema(),
        "quantum_logical_circuit": LogicalCircuitInfo.model_json_schema(),
        "quantum_diagnostics": QuantumDiagnostics.model_json_schema(),
        "quantum_result": QuantumResult.model_json_schema(),
        "quantum_solve_info": QuantumSolveInfo.model_json_schema(),
    }
