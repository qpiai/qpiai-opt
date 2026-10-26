"""Public orchestration helpers for gate-based optimization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import numpy as np

from ._shared import _circuit_info, _pauli_terms, _problem_context, _quantum_types
from .algorithms import ADAPTQAOA, QAOA, VQE, RecursiveQAOA, SamplingVQE
from .types import (
    CVaR,
    QuantumDiagnostics,
    QuantumResult,
    QuantumRun,
    QuantumSample,
    QuantumSolveInfo,
    WarmStart,
    XYMixer,
)


def build_circuit(
    problem: Any,
    *,
    algorithm: Any,
    parameters: Sequence[float] | None = None,
    measurements: bool = False,
):
    """Build an inspectable logical circuit without executing it."""

    if isinstance(algorithm, (ADAPTQAOA, RecursiveQAOA)):
        raise ValueError(
            f"{type(algorithm).__name__} has no static circuit before its adaptive run completes"
        )
    if not isinstance(algorithm, (QAOA, VQE, SamplingVQE)):
        raise TypeError("algorithm must be QAOA, VQE, or SamplingVQE")
    if not isinstance(measurements, bool):
        raise TypeError("measurements must be a boolean")
    if parameters is None:
        parameters = algorithm.initial_point
    if parameters is None:
        raise ValueError(
            "parameters are required because qpiai_quantum circuits use numeric angles"
        )
    return algorithm.build_circuit(problem, parameters, measurements=measurements)


def _vqe_report(problem: Any, algorithm: VQE) -> dict[str, Any]:
    hamiltonian, terms, n_qubits, _ = algorithm._input(problem)
    diagonal = not any(pauli in {"X", "Y"} for operators, _ in terms for _, pauli in operators)
    if isinstance(algorithm, SamplingVQE) and not diagonal:
        raise ValueError("SamplingVQE requires a diagonal computational-basis Hamiltonian")
    solver = algorithm._solver(hamiltonian, n_qubits)
    ansatz = algorithm._ansatz_template(solver, n_qubits)
    parameter_count = sum(
        getattr(getattr(operation, "operation_type", None), "name", None) == "N_QUBIT_PARAMETRIC"
        for operation in ansatz.icr.evolve
    )
    if algorithm.initial_point is not None and len(algorithm.initial_point) != parameter_count:
        raise ValueError(
            f"initial_point must contain exactly {parameter_count} finite real numbers"
        )
    circuit = solver._build_circuit(
        ansatz,
        np.zeros(parameter_count, dtype=float),
        measure=False,
    )
    return {
        "algorithm": type(algorithm).__name__,
        "compatible": True,
        "qubits": n_qubits,
        "hamiltonian_terms": len(terms),
        "linear_terms": sum(len(operators) == 1 for operators, _ in terms),
        "quadratic_terms": sum(len(operators) == 2 for operators, _ in terms),
        "max_locality": max((len(operators) for operators, _ in terms), default=0),
        "diagonal": diagonal,
        "parameter_count": parameter_count,
        "ansatz": algorithm.ansatz if isinstance(algorithm.ansatz, str) else "custom",
        "optimizer": algorithm.optimizer.lower().replace("-", "_"),
        "logical_circuit": _circuit_info(circuit).model_dump(mode="json"),
        **(
            {
                "aggregation": (
                    algorithm.aggregation.alpha if algorithm.aggregation else "expectation"
                )
            }
            if isinstance(algorithm, SamplingVQE)
            else {}
        ),
    }


def algorithm_report(problem: Any, algorithm: Any) -> dict[str, Any]:
    """Return deterministic pre-execution quantum structure and compatibility."""

    if isinstance(algorithm, RecursiveQAOA):
        context = _problem_context(problem)
        if not context.optimization_source:
            raise ValueError("RecursiveQAOA requires explicit optimization QUBO or Ising input")
        if context.ising.num_variables < 1:
            raise ValueError("RecursiveQAOA requires at least one qubit")
        return {
            "algorithm": "RecursiveQAOA",
            "compatible": True,
            "qubits": context.ising.num_variables,
            "hamiltonian_terms": len(_pauli_terms(context.ising)),
            "linear_terms": len(context.ising.linear),
            "quadratic_terms": len(context.ising.quadratic),
            "max_locality": 2 if context.ising.quadratic else 1 if context.ising.linear else 0,
            "diagonal": True,
            "min_variables": algorithm.min_variables,
            "eliminations_per_step": algorithm.eliminations_per_step,
            "inner_qaoa": algorithm_report(problem, algorithm.qaoa),
            "logical_circuit": None,
        }
    if isinstance(algorithm, ADAPTQAOA):
        context = _problem_context(problem)
        if context.ising.num_variables < 1:
            raise ValueError("ADAPTQAOA requires at least one qubit")
        pool = algorithm._pool(context.ising.num_variables)
        return {
            "algorithm": "ADAPTQAOA",
            "compatible": True,
            "qubits": context.ising.num_variables,
            "hamiltonian_terms": len(_pauli_terms(context.ising)),
            "linear_terms": len(context.ising.linear),
            "quadratic_terms": len(context.ising.quadratic),
            "max_locality": 2 if context.ising.quadratic else 1 if context.ising.linear else 0,
            "diagonal": True,
            "operator_pool": algorithm.operator_pool
            if isinstance(algorithm.operator_pool, str)
            else "custom",
            "operator_pool_size": len(pool),
            "max_layers": algorithm.max_layers,
            "gradient_tolerance": algorithm.gradient_tolerance,
            "selection_gamma": algorithm.selection_gamma,
            "logical_circuit": None,
        }
    if isinstance(algorithm, QAOA):
        context = _problem_context(problem)
        ising = context.ising
        if ising.num_variables < 1:
            raise ValueError("QAOA requires at least one qubit")
        parameter_count = algorithm.parameter_count(ising)
        if algorithm.initial_point is not None and len(algorithm.initial_point) != parameter_count:
            raise ValueError(
                f"initial_point must contain exactly {parameter_count} finite real numbers"
            )
        if algorithm.warm_start is not None:
            relaxed = algorithm.warm_start.relaxed_solution
            if isinstance(relaxed, Mapping):
                names = ising.variable_names or tuple(range(ising.num_variables))
                if set(relaxed) != set(names):
                    raise ValueError(
                        "warm-start relaxed_solution mapping must cover every Ising variable"
                    )
            elif relaxed is not None and len(relaxed) != ising.num_variables:
                raise ValueError("warm-start relaxed_solution has the wrong length")
        if isinstance(algorithm.mixer, XYMixer) and any(
            qubit >= ising.num_variables for edge in algorithm.mixer.edges() for qubit in edge
        ):
            raise ValueError("XYMixer references a qubit outside the problem register")
        if algorithm.initial_state is not None:
            circuit_type, _, _, _ = _quantum_types()
            if isinstance(algorithm.initial_state, circuit_type):
                if algorithm.initial_state.num_qubits != ising.num_variables:
                    raise ValueError("initial_state circuit has the wrong number of qubits")
                if any(
                    getattr(getattr(operation, "operation_type", None), "name", None) == "MEASURE"
                    for operation in algorithm.initial_state.icr.evolve
                ):
                    raise ValueError("initial_state circuit cannot contain measurements")
            elif not isinstance(algorithm.initial_state, Sequence):
                raise TypeError("initial_state must be a binary sequence or qpiai_quantum.Circuit")
            elif len(algorithm.initial_state) != ising.num_variables:
                raise ValueError("initial_state must be a binary sequence of length num_qubits")
        report = {
            "algorithm": "QAOA",
            "compatible": True,
            "qubits": ising.num_variables,
            "hamiltonian_terms": len(_pauli_terms(ising)),
            "linear_terms": len(ising.linear),
            "quadratic_terms": len(ising.quadratic),
            "max_locality": 2 if ising.quadratic else 1 if ising.linear else 0,
            "diagonal": True,
            "parameter_count": parameter_count,
            "reps": algorithm.reps,
            "parameterization": algorithm.parameterization,
            "mixer": (
                "warm_start"
                if algorithm.warm_start is not None
                else "xy"
                if isinstance(algorithm.mixer, XYMixer)
                else "custom"
                if callable(algorithm.mixer)
                else "x"
            ),
            "aggregation": algorithm.aggregation.alpha if algorithm.aggregation else "expectation",
            "warm_start": algorithm.warm_start.epsilon if algorithm.warm_start else None,
            "initial_state": algorithm.initial_state is not None
            or algorithm.warm_start is not None,
        }
        if callable(algorithm.mixer) and not isinstance(algorithm.mixer, XYMixer):
            report["logical_circuit"] = None
        else:
            circuit_algorithm = algorithm
            if algorithm.warm_start is not None:
                circuit_algorithm = replace(
                    algorithm,
                    warm_start=WarmStart(
                        epsilon=algorithm.warm_start.epsilon,
                        relaxed_solution=[0.5] * ising.num_variables,
                    ),
                )
            circuit = circuit_algorithm.build_circuit(
                problem,
                np.zeros(parameter_count, dtype=float),
                measurements=False,
            )
            report["logical_circuit"] = _circuit_info(circuit).model_dump(mode="json")
        return report
    if isinstance(algorithm, (VQE, SamplingVQE)):
        return _vqe_report(problem, algorithm)
    raise TypeError("unsupported quantum algorithm")


def run_algorithm(
    problem: Any,
    algorithm: Any,
    *,
    mode: str,
    execution: QuantumRun | None,
    client: Any = None,
) -> QuantumResult:
    """Execute one supported quantum algorithm through the canonical solve route."""

    if client is not None:
        raise ValueError(
            "client= authenticates QpiAI Opt Platform, not QCloud; use QpiAIQuantumAuth for gate-based optimization"
        )
    if mode == "auto":
        raise ValueError(
            "mode='auto' is not supported for gate-based optimization algorithms; choose 'local' or 'platform'"
        )
    if not isinstance(algorithm, (QAOA, VQE, SamplingVQE, RecursiveQAOA, ADAPTQAOA)):
        raise TypeError("algorithm must be QAOA, VQE, SamplingVQE, RecursiveQAOA, or ADAPTQAOA")
    return algorithm.run(problem, execution=execution, mode=mode)


__all__ = [
    "ADAPTQAOA",
    "CVaR",
    "QAOA",
    "QuantumDiagnostics",
    "QuantumResult",
    "QuantumRun",
    "QuantumSample",
    "QuantumSolveInfo",
    "RecursiveQAOA",
    "SamplingVQE",
    "VQE",
    "WarmStart",
    "XYMixer",
    "build_circuit",
]
