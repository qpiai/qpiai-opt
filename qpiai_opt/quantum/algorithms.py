"""Gate-based optimization algorithms built on QpiAI Quantum SDK primitives."""

from __future__ import annotations

import itertools
import math
import numbers
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .._numeric import finite_fsum, is_finite_real
from ..opt_modeller.converters import _IsingData
from ..opt_solvers.ising import IsingProblem
from ..opt_solvers.local import (
    _LOCAL_RUNTIME_INSTALL_ERROR,
    NATIVE_EXACT_MAX_VARIABLES,
    NATIVE_EXACT_MAX_WORK,
    _ising_polynomial,
    _polynomial_metrics,
)
from ._shared import (
    PauliOperator,
    PauliString,
    _aggregate_counts,
    _apply_operator_evolution,
    _apply_z_evolution,
    _bit_to_spin,
    _circuit_info,
    _commutator_gradient_terms,
    _compose_initial_state,
    _counts_pauli_expectation,
    _execution_backend,
    _execution_experiment_name,
    _is_qubit_hamiltonian,
    _measure_pauli_terms,
    _n_qubits,
    _optimization_coordinates,
    _optimization_energy,
    _optimize,
    _pauli_terms,
    _problem_context,
    _quantum_types,
    _qubo_energy,
    _qubo_to_ising,
    _raw_execution_metadata,
    _rotation_angle,
    _run_circuit,
    _samples_and_best,
    _seeded_legacy_numpy,
    _spin_to_bitstring,
    _statevector_expectation,
    _terms,
    _validate_counts,
    _verify_qcloud_authentication,
    _warm_start_probabilities,
    convert_to_qubit_hamiltonian,
)
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

_SUPPORTED_OPTIMIZERS = {"cobyla", "spsa", "nelder_mead", "powell"}
_verified_recursive_stage: ContextVar[object | None] = ContextVar(
    "qpiai_opt_verified_recursive_stage", default=None
)


def _validate_optimizer(optimizer: str | Callable, *, custom: bool = True) -> None:
    if callable(optimizer):
        if not custom:
            raise TypeError("this algorithm requires a named quantum-SDK optimizer")
        return
    if (
        not isinstance(optimizer, str)
        or optimizer.lower().replace("-", "_") not in _SUPPORTED_OPTIMIZERS
    ):
        suffix = ", or callable" if custom else ""
        raise ValueError("optimizer must be 'cobyla', 'spsa', 'nelder_mead', or 'powell'" + suffix)


def _validate_positive_integer(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _execution(execution: QuantumRun | None, mode: str) -> QuantumRun:
    if mode not in {"local", "platform"}:
        raise ValueError("mode must be 'local' or 'platform' for gate-based optimization algorithms")
    if execution is None:
        execution = QuantumRun()
    if not isinstance(execution, QuantumRun):
        raise TypeError("execution must be QuantumRun")
    if mode == "local" and execution.timeout is not None:
        raise ValueError("QuantumRun.timeout applies only to platform execution")
    if mode == "local" and execution.device_name is not None:
        raise ValueError("QuantumRun.device_name applies only to platform execution")
    if mode == "platform" and execution.seed is not None:
        raise ValueError("QuantumRun.seed applies only to local execution")
    if mode == "platform" and execution.device_name == "QpiAI-QSV-Local":
        raise ValueError(
            "QpiAI-QSV-Local is an in-process simulator; use mode='local' without device_name"
        )
    _quantum_types()
    return execution


def _optimizer_name(optimizer: str | Callable) -> str:
    if isinstance(optimizer, str):
        return optimizer.lower().replace("-", "_")
    return getattr(optimizer, "__name__", type(optimizer).__name__)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
        raise RuntimeError("optimizer returned an invalid evaluation or iteration count")
    return int(value)


def _finite_vector(value: Any, name: str, expected: int | None = None) -> np.ndarray:
    expected_text = (
        "finite real numbers" if expected is None else f"exactly {expected} finite real numbers"
    )
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError(f"{name} must contain {expected_text}")
    try:
        values = list(value)
    except TypeError:
        raise ValueError(f"{name} must contain {expected_text}") from None
    if (expected is not None and len(values) != expected) or any(
        not is_finite_real(item) for item in values
    ):
        raise ValueError(f"{name} must contain {expected_text}")
    return np.asarray([float(item) for item in values], dtype=float)


def _append_raw_metadata(
    raw: Any,
    job_ids: list[str],
    qpu_metadata: list[dict[str, Any]],
) -> None:
    job_id, metadata = _raw_execution_metadata(raw)
    if job_id:
        job_ids.append(job_id)
    if metadata:
        qpu_metadata.append(metadata)


def _attach_info(
    result: QuantumResult,
    *,
    solver: str,
    method: str,
    transformed: bool,
    termination: str = "completed",
    settings: Mapping[str, int | float | str | bool] | None = None,
) -> QuantumResult:
    usable = result.objective is not None
    result.solve_info = QuantumSolveInfo.model_validate(
        {
            "problem_class": result.problem_class,
            "execution_mode": result.execution_mode,
            "provider": "qpiai-quantum",
            "solver": solver,
            "method": method,
            "termination": termination,
            "has_usable_solution": usable,
            "solution_feasible": True if usable else None,
            "optimality_proven": False,
            "guarantee": "best_found"
            if usable
            else ("unknown" if result.problem_class == "QUBIT_HAMILTONIAN" else "none"),
            "source_objective": result.objective,
            "transformed": transformed,
            "settings": dict(settings or {}),
        }
    )
    return result


def _transformation_records(context: Any, *, pauli_mapping: bool) -> list[dict[str, Any]]:
    records = list(context.transformations)
    if context.problem_class == "QUBO":
        records.append(
            {
                "transformation": "qubo_to_ising",
                "substitution": "x = (1 + s) / 2",
                "original_sense": context.ising.original_sense,
            }
        )
    elif context.problem_class == "QUBIT_HAMILTONIAN":
        records.append(
            {
                "transformation": "qubit_hamiltonian_to_ising_readout",
                "substitution": "s = -z",
                "exact_terms": "identity, Z, and ZZ",
            }
        )
    if pauli_mapping and context.problem_class != "QUBIT_HAMILTONIAN":
        records.append(
            {
                "transformation": "ising_to_qubit_hamiltonian",
                "substitution": "z = -s",
                "exact_terms": "identity, Z, and ZZ",
            }
        )
    return records


@dataclass(frozen=True)
class QAOA:
    """Conventional shared-angle or multi-angle QAOA."""

    reps: int = 1
    parameterization: str = "standard"
    optimizer: str | Callable = "cobyla"
    max_iterations: int = 100
    initial_point: Sequence[float] | None = None
    aggregation: CVaR | None = None
    warm_start: WarmStart | None = None
    mixer: XYMixer | Callable | None = None
    initial_state: Sequence[int] | Any | None = None
    _warm_cache: tuple[tuple[Any, ...], list[float], dict[str, Any]] | None = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self):
        _validate_positive_integer(self.reps, "reps")
        _validate_positive_integer(self.max_iterations, "max_iterations")
        if not isinstance(self.parameterization, str):
            raise TypeError("parameterization must be 'standard' or 'multi_angle'")
        object.__setattr__(
            self,
            "parameterization",
            self.parameterization.lower().replace("-", "_"),
        )
        if self.parameterization not in {"standard", "multi_angle"}:
            raise ValueError("parameterization must be 'standard' or 'multi_angle'")
        _validate_optimizer(self.optimizer)
        if self.aggregation is not None and not isinstance(self.aggregation, CVaR):
            raise TypeError("aggregation must be CVaR or None")
        if self.warm_start is not None and not isinstance(self.warm_start, WarmStart):
            raise TypeError("warm_start must be WarmStart or None")
        if (
            self.mixer is not None
            and not isinstance(self.mixer, XYMixer)
            and not callable(self.mixer)
        ):
            raise TypeError("mixer must be XYMixer, callable, or None")
        if self.warm_start is not None and self.initial_state is not None:
            raise ValueError("warm_start cannot be combined with an explicit initial_state")
        if self.warm_start is not None and self.mixer is not None:
            raise ValueError("warm_start supplies its own tilted mixer and cannot use mixer=")
        if isinstance(self.mixer, XYMixer) and self.initial_state is None:
            raise ValueError("XYMixer requires an explicit feasible initial_state")
        if callable(self.mixer) and self.parameterization == "multi_angle":
            raise ValueError("a custom mixer has no inferred multi-angle parameter layout")
        if self.initial_point is not None:
            object.__setattr__(
                self,
                "initial_point",
                tuple(_finite_vector(self.initial_point, "initial_point")),
            )
        if isinstance(self.initial_state, Sequence) and not isinstance(
            self.initial_state, (str, bytes)
        ):
            initial_state = tuple(self.initial_state)
            if any(value not in (0, 1, False, True) for value in initial_state):
                raise ValueError("initial_state must be a binary sequence")
            object.__setattr__(self, "initial_state", initial_state)

    def _warm_data(self, ising: _IsingData) -> tuple[list[float] | None, dict[str, Any]]:
        if self.warm_start is None:
            return None, {}
        relaxed = self.warm_start.relaxed_solution
        if isinstance(relaxed, Mapping):
            names = ising.variable_names or tuple(range(ising.num_variables))
            if set(relaxed) != set(names):
                raise ValueError(
                    "warm-start relaxed_solution mapping must cover every Ising variable"
                )
            relaxed_key = tuple(float(relaxed[name]) for name in names)
        elif relaxed is None:
            relaxed_key = None
        else:
            relaxed_key = tuple(float(value) for value in relaxed)
        key = (
            ising.num_variables,
            ising.offset,
            tuple(sorted(ising.linear.items())),
            tuple(sorted(ising.quadratic.items())),
            ising.variable_names,
            ising.original_sense,
            self.warm_start.epsilon,
            relaxed_key,
        )
        if self._warm_cache is not None and self._warm_cache[0] == key:
            return list(self._warm_cache[1]), dict(self._warm_cache[2])
        probabilities, metadata = _warm_start_probabilities(self.warm_start, ising)
        object.__setattr__(
            self,
            "_warm_cache",
            (key, list(probabilities), dict(metadata)),
        )
        return probabilities, metadata

    def parameter_count(self, ising: _IsingData) -> int:
        ising = _qubo_to_ising(ising)
        if self.parameterization == "standard":
            return 2 * self.reps
        cost_terms = len(ising.linear) + len(ising.quadratic)
        if isinstance(self.mixer, XYMixer):
            mixer_terms = len(self.mixer.edges())
        else:
            mixer_terms = ising.num_variables
        return self.reps * (cost_terms + mixer_terms)

    def _apply_mixer(
        self,
        circuit: Any,
        beta: float,
        layer: int,
        n_qubits: int,
        warm_probabilities: Sequence[float] | None,
    ) -> Any:
        circuit_type, _, _, _ = _quantum_types()
        if callable(self.mixer) and not isinstance(self.mixer, XYMixer):
            updated = self.mixer(circuit, beta, layer)
            if updated is not None:
                if not isinstance(updated, circuit_type) or updated.num_qubits != n_qubits:
                    raise TypeError(
                        "custom mixer must mutate the supplied Circuit or return a same-width Circuit"
                    )
                if updated is not circuit:
                    raise ValueError(
                        "custom mixer must preserve the supplied circuit and may return only that same object"
                    )
            if any(
                getattr(getattr(operation, "operation_type", None), "name", None) == "MEASURE"
                for operation in circuit.icr.evolve
            ):
                raise ValueError("custom mixer cannot add measurements")
            return circuit
        if isinstance(self.mixer, XYMixer):
            for left, right in self.mixer.edges():
                angle = _rotation_angle(beta, 2.0)
                circuit.rxx(left, right, angle)
                circuit.ryy(left, right, angle)
            return circuit
        for qubit in range(n_qubits):
            if warm_probabilities is None:
                circuit.rx(qubit, _rotation_angle(beta, 2.0))
            else:
                theta = 2.0 * math.asin(math.sqrt(warm_probabilities[qubit]))
                # This is exp[-i beta(sin(theta) X + cos(theta) Z)]. The sign-equivalent
                # literature mixer uses the negative Hamiltonian and beta -> -beta.
                circuit.ry(qubit, -theta)
                circuit.rz(qubit, _rotation_angle(beta, 2.0))
                circuit.ry(qubit, theta)
        return circuit

    def build_circuit(
        self,
        problem: Any,
        parameters: Sequence[float],
        measurements: bool = False,
    ):
        circuit_type, _, _, _ = _quantum_types()
        ising = _qubo_to_ising(problem)
        n_qubits = ising.num_variables
        if n_qubits < 1:
            raise ValueError("QAOA requires at least one qubit")
        if isinstance(self.mixer, XYMixer) and any(
            qubit >= n_qubits for edge in self.mixer.edges() for qubit in edge
        ):
            raise ValueError("XYMixer references a qubit outside the problem register")
        expected = self.parameter_count(ising)
        values = _finite_vector(parameters, "parameters", expected)
        circuit = circuit_type(n_qubits)
        warm_probabilities, _ = self._warm_data(ising)
        if warm_probabilities is None:
            _compose_initial_state(circuit, self.initial_state, n_qubits)
        else:
            for qubit, probability in enumerate(warm_probabilities):
                circuit.ry(qubit, 2.0 * math.asin(math.sqrt(probability)))

        if self.parameterization == "standard":
            gammas = values[: self.reps]
            betas = values[self.reps :]
            for layer, (gamma, beta) in enumerate(zip(gammas, betas)):
                _apply_z_evolution(circuit, ising, float(gamma))
                circuit = self._apply_mixer(
                    circuit,
                    float(beta),
                    layer,
                    n_qubits,
                    warm_probabilities,
                )
        else:
            position = 0
            for layer in range(self.reps):
                for qubit, coefficient in sorted(ising.linear.items()):
                    circuit.rz(
                        qubit,
                        _rotation_angle(float(values[position]), coefficient, -2.0),
                    )
                    position += 1
                for (left, right), coefficient in sorted(ising.quadratic.items()):
                    circuit.rzz(
                        left,
                        right,
                        _rotation_angle(float(values[position]), coefficient, 2.0),
                    )
                    position += 1
                if isinstance(self.mixer, XYMixer):
                    for left, right in self.mixer.edges():
                        beta = float(values[position])
                        position += 1
                        angle = _rotation_angle(beta, 2.0)
                        circuit.rxx(left, right, angle)
                        circuit.ryy(left, right, angle)
                else:
                    for qubit in range(n_qubits):
                        beta = float(values[position])
                        position += 1
                        if warm_probabilities is None:
                            circuit.rx(qubit, _rotation_angle(beta, 2.0))
                        else:
                            theta = 2.0 * math.asin(math.sqrt(warm_probabilities[qubit]))
                            circuit.ry(qubit, -theta)
                            circuit.rz(qubit, _rotation_angle(beta, 2.0))
                            circuit.ry(qubit, theta)
            if position != len(values):
                raise RuntimeError("internal multi-angle parameter ordering error")
        if measurements:
            circuit.measure_all()
        return circuit

    def run(
        self,
        problem: Any,
        *,
        execution: QuantumRun | None = None,
        mode: str = "local",
    ) -> QuantumResult:
        started = time.perf_counter()
        execution = _execution(execution, mode)
        if mode == "local":
            if _verified_recursive_stage.get() is self:
                # Consume this stage's handoff before user callbacks can start another solve.
                _verified_recursive_stage.set(None)
            else:
                _verify_qcloud_authentication()
        context = _problem_context(problem)
        ising = context.ising
        n_parameters = self.parameter_count(ising)
        initial = (
            np.zeros(n_parameters, dtype=float)
            if self.initial_point is None
            else _finite_vector(self.initial_point, "initial_point", n_parameters)
        )
        objective_history = []
        observed_counts: dict[str, int] = defaultdict(int)
        final_counts = {}
        job_ids: list[str] = []
        qpu_metadata: list[dict[str, Any]] = []

        def energy(bitstring: str) -> float:
            return _optimization_energy(context, bitstring)

        def objective(parameters: np.ndarray) -> float:
            circuit = self.build_circuit(ising, parameters, measurements=True)
            raw = _run_circuit(circuit, execution, mode)
            counts = _validate_counts(dict(raw.counts or {}), ising.num_variables)
            for bitstring, count in counts.items():
                observed_counts[bitstring] += count
            _append_raw_metadata(raw, job_ids, qpu_metadata)
            value = _aggregate_counts(counts, energy, self.aggregation)
            objective_history.append(value)
            return value

        solver_started = time.perf_counter()
        parameters, optimum, optimizer_info = _optimize(
            objective,
            initial,
            self.optimizer,
            self.max_iterations,
            seed=execution.seed,
        )
        final_circuit = self.build_circuit(ising, parameters, measurements=True)
        final_raw = _run_circuit(final_circuit, execution, mode)
        final_counts = _validate_counts(dict(final_raw.counts or {}), ising.num_variables)
        for bitstring, count in final_counts.items():
            observed_counts[bitstring] += count
        _append_raw_metadata(final_raw, job_ids, qpu_metadata)
        solver_elapsed = time.perf_counter() - solver_started
        samples, best, best_generic = _samples_and_best(context, observed_counts)
        warm_probabilities, warm_metadata = self._warm_data(ising)
        diagnostics = QuantumDiagnostics(
            algorithm="QAOA",
            backend=_execution_backend(execution, mode),
            shots=execution.shots,
            optimizer=_optimizer_name(self.optimizer),
            evaluations=_optional_int(optimizer_info.get("nfev")),
            iterations=_optional_int(optimizer_info.get("nit")),
            optimized_parameters=parameters.tolist(),
            objective_history=objective_history,
            parameter_history=[
                np.asarray(values, dtype=float).tolist()
                for values in optimizer_info.get("param_history", [])
            ],
            counts=final_counts,
            candidates=samples,
            job_ids=job_ids,
            logical_circuit=_circuit_info(final_circuit),
            qpu_metadata=qpu_metadata,
            transformations=_transformation_records(context, pauli_mapping=True),
            algorithm_metadata={
                "reps": self.reps,
                "parameterization": self.parameterization,
                "parameter_order": (
                    "gamma[0:p], beta[0:p]"
                    if self.parameterization == "standard"
                    else "per layer: sorted Z terms, sorted ZZ terms, then mixer generators"
                ),
                "mixer": (
                    "warm_start"
                    if warm_probabilities is not None
                    else "xy"
                    if isinstance(self.mixer, XYMixer)
                    else "custom"
                    if callable(self.mixer)
                    else "x"
                ),
                "aggregation": self.aggregation.alpha if self.aggregation else "expectation",
                "optimized_aggregate": optimum,
                "optimization_offset": context.optimization_offset,
                "candidate_distribution": "all variational and final measurement evaluations",
                **warm_metadata,
            },
        )
        if context.optimization_source and best is not None:
            objective_value, solution, assignment = best
            result = QuantumResult(
                problem_class=context.problem_class,
                execution_mode=mode,
                objective=objective_value,
                solution=solution,
                variable_assignment=assignment,
                quantum=diagnostics,
                solver_time_s=solver_elapsed,
                compute_time_s=time.perf_counter() - started,
            )
        else:
            result = QuantumResult(
                problem_class=context.problem_class,
                execution_mode=mode,
                eigenvalue=(best_generic if context.problem_class == "QUBIT_HAMILTONIAN" else None),
                quantum=diagnostics,
                solver_time_s=solver_elapsed,
                compute_time_s=time.perf_counter() - started,
            )
        return _attach_info(
            result,
            solver="QAOA",
            method="sampled variational optimization",
            transformed=context.transformed,
            settings={"shots": execution.shots, "reps": self.reps, "parameters": n_parameters},
        )


@dataclass(frozen=True)
class VQE:
    """Optimization-facing adapter over ``qpiai_quantum.VQESolver``."""

    ansatz: str | Callable | Any = "standard"
    optimizer: str = "cobyla"
    max_iterations: int = 100
    initial_point: Sequence[float] | None = None

    def __post_init__(self):
        _validate_positive_integer(self.max_iterations, "max_iterations")
        _validate_optimizer(self.optimizer, custom=False)
        if self.initial_point is not None:
            object.__setattr__(
                self,
                "initial_point",
                tuple(_finite_vector(self.initial_point, "initial_point")),
            )

    def _input(self, problem: Any):
        if _is_qubit_hamiltonian(problem):
            hamiltonian = problem
            context = None
        else:
            context = _problem_context(problem)
            hamiltonian = convert_to_qubit_hamiltonian(context.problem)
        terms = _terms(hamiltonian)
        n_qubits = _n_qubits(terms, hamiltonian)
        if n_qubits < 1:
            raise ValueError("VQE requires a Hamiltonian acting on at least one qubit")
        _, hamiltonian_type, _, _ = _quantum_types()
        canonical = hamiltonian_type(terms)
        canonical.n_qubits = n_qubits
        canonical.variable_names = tuple(getattr(hamiltonian, "variable_names", ()))
        canonical.original_sense = getattr(hamiltonian, "original_sense", "MIN")
        return canonical, terms, n_qubits, context

    def _solver(self, hamiltonian: Any, n_qubits: int):
        from qpiai_quantum.algorithms.opt.solvers import VQESolver

        circuit_type, _, _, _ = _quantum_types()
        ansatz = self.ansatz
        if isinstance(ansatz, str):
            ansatz = ansatz.lower().replace("-", "_")
            if ansatz not in {"standard", "hardware_efficient", "two_local"}:
                raise ValueError(
                    "ansatz must be 'standard', 'hardware_efficient', 'two_local', Circuit, or callable"
                )
        elif isinstance(ansatz, circuit_type):
            template = ansatz

            def ansatz(_n_qubits, circuit=template):
                if circuit.num_qubits != _n_qubits:
                    raise ValueError("custom ansatz circuit has the wrong number of qubits")
                return circuit

        elif not callable(ansatz):
            raise TypeError("ansatz must be a built-in name, Circuit, or callable")
        solver = VQESolver(
            n_qubits=n_qubits,
            hamiltonian=hamiltonian,
            ansatz=ansatz,
            optimizer=self.optimizer.lower().replace("-", "_"),
            max_iterations=self.max_iterations,
            initial_point=(
                None
                if self.initial_point is None
                else _finite_vector(self.initial_point, "initial_point")
            ),
            verbose=False,
        )
        solver._get_n_qubits_from_hamiltonian = lambda _hamiltonian: n_qubits
        return solver

    def _ansatz_template(self, solver: Any, n_qubits: int):
        ansatz = solver._get_ansatz()
        if ansatz.num_qubits != n_qubits:
            raise ValueError("ansatz circuit has the wrong number of qubits")
        parameter_count = 0
        for operation in ansatz.icr.evolve:
            operation_type = getattr(getattr(operation, "operation_type", None), "name", None)
            if operation_type == "MEASURE":
                raise ValueError("ansatz circuit cannot contain measurements")
            if operation_type == "N_QUBIT_PARAMETRIC" and len(operation.params or []) != 1:
                raise ValueError(
                    "custom ansatz parametric gates must each contain exactly one bindable angle"
                )
            if operation_type == "N_QUBIT_PARAMETRIC":
                parameter_count += 1
        if parameter_count == 0:
            raise ValueError("ansatz circuit must contain at least one bindable parameter")
        return ansatz

    def build_circuit(
        self,
        problem: Any,
        parameters: Sequence[float],
        measurements: bool = False,
    ):
        hamiltonian, _, n_qubits, _ = self._input(problem)
        solver = self._solver(hamiltonian, n_qubits)
        ansatz = self._ansatz_template(solver, n_qubits)
        expected = sum(
            getattr(getattr(operation, "operation_type", None), "name", None)
            == "N_QUBIT_PARAMETRIC"
            for operation in ansatz.icr.evolve
        )
        values = _finite_vector(parameters, "parameters", expected)
        return solver._build_circuit(ansatz, values, measure=measurements)

    def run(
        self,
        problem: Any,
        *,
        execution: QuantumRun | None = None,
        mode: str = "local",
    ) -> QuantumResult:
        started = time.perf_counter()
        execution = _execution(execution, mode)
        if mode == "local":
            _verify_qcloud_authentication()
        hamiltonian, terms, n_qubits, context = self._input(problem)
        if context is not None and context.optimization_offset:
            hamiltonian = convert_to_qubit_hamiltonian(
                context.problem.model_copy(update={"offset": 0.0})
            )
            terms = _terms(hamiltonian)
        solver = self._solver(hamiltonian, n_qubits)
        ansatz_template = self._ansatz_template(solver, n_qubits)
        solver.ansatz = lambda _n_qubits, template=ansatz_template: template
        n_parameters = sum(
            getattr(getattr(operation, "operation_type", None), "name", None)
            == "N_QUBIT_PARAMETRIC"
            for operation in ansatz_template.icr.evolve
        )
        if solver.initial_point is not None and len(solver.initial_point) != n_parameters:
            raise ValueError(
                f"initial_point must contain exactly {n_parameters} finite real numbers"
            )
        if solver.initial_point is None and execution.seed is not None:
            solver.initial_point = np.random.default_rng(execution.seed).uniform(
                0.0, 2.0 * np.pi, n_parameters
            )
        observed_counts: dict[str, int] = defaultdict(int)
        job_ids: list[str] = []
        qpu_metadata: list[dict[str, Any]] = []
        executed_circuits = []

        def execute(circuit, shots=None, **_kwargs):
            executed_circuits.append(circuit)
            if mode == "local":
                _, _, _, simulator_type = _quantum_types()
                raw = simulator_type().run(
                    circuit,
                    shots=shots or execution.shots,
                    seed=execution.seed,
                    name=execution.experiment_name,
                )
            else:
                run_kwargs = {
                    "shots": shots or execution.shots,
                    "device_name": _execution_backend(execution, mode),
                    "experiment_name": _execution_experiment_name(execution),
                }
                if execution.timeout is not None:
                    run_kwargs["timeout"] = execution.timeout
                raw = circuit.run(**run_kwargs)
            raw_counts = getattr(raw, "counts", None)
            if isinstance(raw_counts, Mapping):
                for bitstring, count in raw_counts.items():
                    if (
                        isinstance(bitstring, str)
                        and len(bitstring) == n_qubits
                        and set(bitstring) <= {"0", "1"}
                        and isinstance(count, int)
                        and not isinstance(count, bool)
                        and count > 0
                    ):
                        observed_counts[bitstring] += count
            _append_raw_metadata(raw, job_ids, qpu_metadata)
            return raw

        solver._execute_circuit = execute
        solver._compute_statevector_expectation = _statevector_expectation

        def counts_expectation(counts, grouped_terms):
            validated = _validate_counts(dict(counts or {}), n_qubits)
            return _counts_pauli_expectation(validated, grouped_terms)

        solver._compute_counts_expectation = counts_expectation

        def measured_expectation(result):
            counts = _validate_counts(dict(result.counts or {}), n_qubits)
            return _counts_pauli_expectation(counts, terms)

        solver._compute_expectation = measured_expectation
        # This selects the upstream measurement path; execute() owns the actual backend.
        solver_device_name = "QpiAI-QSV-Simulator" if mode == "local" else "QpiAI-QSV-Local"
        solver_started = time.perf_counter()
        with _seeded_legacy_numpy(
            execution.seed if self.optimizer.lower().replace("-", "_") == "spsa" else None
        ):
            raw = solver.run(
                shots=execution.shots,
                experiment_name=_execution_experiment_name(execution),
                device_name=solver_device_name,
            )
        solver_elapsed = time.perf_counter() - solver_started
        final_counts = _validate_counts(dict(raw.counts or {}), n_qubits) if raw.counts else {}
        samples = []
        best = None
        if context is not None:
            samples, best, _ = _samples_and_best(context, observed_counts)
        final_circuit = executed_circuits[-1] if executed_circuits else None
        metadata = dict(raw.metadata or {})
        diagnostics = QuantumDiagnostics(
            algorithm="VQE",
            backend=_execution_backend(execution, mode),
            shots=execution.shots,
            optimizer=self.optimizer.lower().replace("-", "_"),
            evaluations=_optional_int(metadata.get("circuit_evaluations")),
            iterations=_optional_int(metadata.get("actual_iterations")),
            optimized_parameters=list(raw.optimal_parameters),
            objective_history=list(raw.energy_history),
            parameter_history=list(raw.param_history),
            counts=final_counts,
            candidates=samples,
            job_ids=job_ids,
            logical_circuit=_circuit_info(final_circuit) if final_circuit is not None else None,
            qpu_metadata=qpu_metadata,
            transformations=(
                _transformation_records(context, pauli_mapping=True) if context is not None else []
            ),
            algorithm_metadata={
                "ansatz": self.ansatz if isinstance(self.ansatz, str) else "custom",
                "variational_energy": float(raw.optimal_energy),
                **(
                    {"optimization_offset": context.optimization_offset}
                    if context is not None
                    else {}
                ),
                "optimizer_message": str(metadata.get("message", "")),
                "candidate_distribution": "all computational-basis measurements",
            },
        )
        if context is not None and best is not None:
            objective_value, solution, assignment = best
            result = QuantumResult(
                problem_class=context.problem_class,
                execution_mode=mode,
                objective=objective_value,
                solution=solution,
                variable_assignment=assignment,
                quantum=diagnostics,
                solver_time_s=solver_elapsed,
                compute_time_s=time.perf_counter() - started,
            )
        elif context is not None:
            result = QuantumResult(
                problem_class=context.problem_class,
                execution_mode=mode,
                quantum=diagnostics,
                solver_time_s=solver_elapsed,
                compute_time_s=time.perf_counter() - started,
            )
        else:
            result = QuantumResult(
                problem_class="QUBIT_HAMILTONIAN",
                execution_mode=mode,
                eigenvalue=float(raw.optimal_energy),
                quantum=diagnostics,
                solver_time_s=solver_elapsed,
                compute_time_s=time.perf_counter() - started,
            )
        return _attach_info(
            result,
            solver="VQE",
            method="variational eigensolver",
            transformed=context.transformed if context is not None else False,
            settings={"shots": execution.shots, "parameters": n_parameters},
        )


@dataclass(frozen=True)
class SamplingVQE(VQE):
    """Sampling variational eigensolver for diagonal objective Hamiltonians."""

    aggregation: CVaR | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.aggregation is not None and not isinstance(self.aggregation, CVaR):
            raise TypeError("aggregation must be CVaR or None")

    def build_circuit(
        self,
        problem: Any,
        parameters: Sequence[float],
        measurements: bool = False,
    ):
        _, terms, _, _ = self._input(problem)
        if any(pauli in {"X", "Y"} for operators, _ in terms for _, pauli in operators):
            raise ValueError("SamplingVQE requires a diagonal computational-basis Hamiltonian")
        return super().build_circuit(problem, parameters, measurements)

    def run(
        self,
        problem: Any,
        *,
        execution: QuantumRun | None = None,
        mode: str = "local",
    ) -> QuantumResult:
        started = time.perf_counter()
        execution = _execution(execution, mode)
        if mode == "local":
            _verify_qcloud_authentication()
        hamiltonian, terms, n_qubits, context = self._input(problem)
        if any(pauli in {"X", "Y"} for operators, _ in terms for _, pauli in operators):
            raise ValueError("SamplingVQE requires a diagonal computational-basis Hamiltonian")
        solver = self._solver(hamiltonian, n_qubits)
        ansatz = self._ansatz_template(solver, n_qubits)
        n_parameters = sum(
            getattr(getattr(operation, "operation_type", None), "name", None)
            == "N_QUBIT_PARAMETRIC"
            for operation in ansatz.icr.evolve
        )
        if self.initial_point is None:
            initial = np.random.default_rng(execution.seed).uniform(0.0, 2.0 * np.pi, n_parameters)
        else:
            initial = _finite_vector(self.initial_point, "initial_point", n_parameters)
        observed_counts: dict[str, int] = defaultdict(int)
        objective_history = []
        job_ids: list[str] = []
        qpu_metadata: list[dict[str, Any]] = []

        def energy(bitstring: str) -> float:
            if context is not None:
                return _optimization_energy(context, bitstring)
            spins = _bit_to_spin(bitstring, n_qubits)
            contributions = []
            for operators, coefficient in terms:
                eigenvalue = math.prod(-spins[qubit] for qubit, _ in operators)
                contributions.append(coefficient * eigenvalue)
            return finite_fsum(contributions) if contributions else 0.0

        def objective(parameters: np.ndarray) -> float:
            circuit = solver._build_circuit(ansatz, np.asarray(parameters), measure=True)
            raw = _run_circuit(circuit, execution, mode)
            counts = _validate_counts(dict(raw.counts or {}), n_qubits)
            for bitstring, count in counts.items():
                observed_counts[bitstring] += count
            _append_raw_metadata(raw, job_ids, qpu_metadata)
            value = _aggregate_counts(counts, energy, self.aggregation)
            objective_history.append(value)
            return value

        solver_started = time.perf_counter()
        parameters, optimum, optimizer_info = _optimize(
            objective,
            initial,
            self.optimizer,
            self.max_iterations,
            seed=execution.seed,
        )
        final_circuit = solver._build_circuit(ansatz, parameters, measure=True)
        final_raw = _run_circuit(final_circuit, execution, mode)
        final_counts = _validate_counts(dict(final_raw.counts or {}), n_qubits)
        for bitstring, count in final_counts.items():
            observed_counts[bitstring] += count
        _append_raw_metadata(final_raw, job_ids, qpu_metadata)
        solver_elapsed = time.perf_counter() - solver_started
        if context is not None:
            samples, best, best_generic = _samples_and_best(context, observed_counts)
        else:
            total = sum(observed_counts.values())
            samples = [
                QuantumSample(
                    bitstring=bitstring,
                    count=count,
                    probability=count / total,
                    internal_energy=energy(bitstring),
                )
                for bitstring, count in sorted(observed_counts.items())
            ]
            best = None
            best_generic = min((sample.internal_energy for sample in samples), default=None)
        diagnostics = QuantumDiagnostics(
            algorithm="SamplingVQE",
            backend=_execution_backend(execution, mode),
            shots=execution.shots,
            optimizer=self.optimizer.lower().replace("-", "_"),
            evaluations=_optional_int(optimizer_info.get("nfev")),
            iterations=_optional_int(optimizer_info.get("nit")),
            optimized_parameters=parameters.tolist(),
            objective_history=objective_history,
            parameter_history=[
                np.asarray(values, dtype=float).tolist()
                for values in optimizer_info.get("param_history", [])
            ],
            counts=final_counts,
            candidates=samples,
            job_ids=job_ids,
            logical_circuit=_circuit_info(final_circuit),
            qpu_metadata=qpu_metadata,
            transformations=(
                _transformation_records(context, pauli_mapping=True) if context is not None else []
            ),
            algorithm_metadata={
                "ansatz": self.ansatz if isinstance(self.ansatz, str) else "custom",
                "aggregation": self.aggregation.alpha if self.aggregation else "expectation",
                "optimized_aggregate": optimum,
                **(
                    {"optimization_offset": context.optimization_offset}
                    if context is not None
                    else {}
                ),
                "candidate_distribution": "all variational and final measurement evaluations",
            },
        )
        if context is not None and best is not None:
            objective_value, solution, assignment = best
            result = QuantumResult(
                problem_class=context.problem_class,
                execution_mode=mode,
                objective=objective_value,
                solution=solution,
                variable_assignment=assignment,
                quantum=diagnostics,
                solver_time_s=solver_elapsed,
                compute_time_s=time.perf_counter() - started,
            )
        else:
            result = QuantumResult(
                problem_class=context.problem_class if context is not None else "QUBIT_HAMILTONIAN",
                execution_mode=mode,
                eigenvalue=(
                    best_generic
                    if context is None or context.problem_class == "QUBIT_HAMILTONIAN"
                    else None
                ),
                quantum=diagnostics,
                solver_time_s=solver_elapsed,
                compute_time_s=time.perf_counter() - started,
            )
        return _attach_info(
            result,
            solver="SamplingVQE",
            method="sampled variational optimization",
            transformed=context.transformed if context is not None else False,
            settings={"shots": execution.shots, "parameters": n_parameters},
        )


def _operator_label(operator: PauliOperator) -> str:
    return " + ".join("".join(f"{pauli}{qubit}" for qubit, pauli in term) for term in operator)


def _validate_pauli_string(raw: Any, n_qubits: int | None) -> PauliString:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or not raw:
        raise ValueError("custom pool operators must be non-empty Pauli strings")
    operators = []
    seen = set()
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("custom pool Pauli entries must be (qubit, Pauli) pairs")
        qubit, pauli = item
        if (
            isinstance(qubit, bool)
            or not isinstance(qubit, int)
            or qubit < 0
            or (n_qubits is not None and qubit >= n_qubits)
        ):
            if n_qubits is None:
                raise ValueError("custom pool qubit indices must be non-negative integers")
            raise ValueError("custom pool qubit indices must lie in the problem register")
        if qubit in seen:
            raise ValueError("a custom pool Pauli string cannot repeat a qubit")
        seen.add(qubit)
        if not isinstance(pauli, str) or pauli.upper() not in {"X", "Y", "Z"}:
            raise ValueError("custom pool entries support only X, Y, and Z")
        operators.append((qubit, pauli.upper()))
    return tuple(sorted(operators))


@dataclass(frozen=True)
class ADAPTQAOA:
    """Gradient-selected ADAPT-QAOA following Zhu et al."""

    operator_pool: str | Sequence[Any] = "single"
    optimizer: str | Callable = "nelder_mead"
    max_iterations: int = 100
    max_layers: int = 10
    gradient_tolerance: float = 1e-3
    selection_gamma: float = 0.01

    def __post_init__(self):
        _validate_positive_integer(self.max_iterations, "max_iterations")
        _validate_positive_integer(self.max_layers, "max_layers")
        _validate_optimizer(self.optimizer)
        if not is_finite_real(self.gradient_tolerance) or self.gradient_tolerance <= 0:
            raise ValueError("gradient_tolerance must be a finite positive number")
        object.__setattr__(self, "gradient_tolerance", float(self.gradient_tolerance))
        if not is_finite_real(self.selection_gamma) or self.selection_gamma <= 0:
            raise ValueError("selection_gamma must be a finite positive number")
        object.__setattr__(self, "selection_gamma", float(self.selection_gamma))
        if isinstance(self.operator_pool, str):
            object.__setattr__(self, "operator_pool", self.operator_pool.lower())
            if self.operator_pool not in {"single", "multi"}:
                raise ValueError("operator_pool must be 'single', 'multi', or a custom sequence")
        elif isinstance(self.operator_pool, (str, bytes)) or not isinstance(
            self.operator_pool, Sequence
        ):
            raise TypeError("operator_pool must be 'single', 'multi', or a custom sequence")
        else:
            pool = tuple(_validate_pauli_string(item, None) for item in self.operator_pool)
            if not pool:
                raise ValueError("custom operator_pool cannot be empty")
            if len(set(pool)) != len(pool):
                raise ValueError("custom operator_pool cannot contain duplicate operators")
            object.__setattr__(self, "operator_pool", pool)

    def _pool(self, n_qubits: int) -> list[PauliOperator]:
        if not isinstance(self.operator_pool, str):
            pool = [(_validate_pauli_string(item, n_qubits),) for item in self.operator_pool]
            if not pool:
                raise ValueError("custom operator_pool cannot be empty")
            if len(set(pool)) != len(pool):
                raise ValueError("custom operator_pool cannot contain duplicate operators")
            return pool
        pool: list[PauliOperator] = []
        pool.extend((((qubit, "X"),),) for qubit in range(n_qubits))
        pool.extend((((qubit, "Y"),),) for qubit in range(n_qubits))
        pool.append(tuple((((qubit, "X"),) for qubit in range(n_qubits))))
        pool.append(tuple((((qubit, "Y"),) for qubit in range(n_qubits))))
        if self.operator_pool == "multi":
            for left, right in itertools.combinations(range(n_qubits), 2):
                for left_pauli in "XYZ":
                    for right_pauli in "XYZ":
                        pool.append((((left, left_pauli), (right, right_pauli)),))
        deduplicated = []
        for operator in pool:
            if operator not in deduplicated:
                deduplicated.append(operator)
        return deduplicated

    def run(
        self,
        problem: Any,
        *,
        execution: QuantumRun | None = None,
        mode: str = "local",
    ) -> QuantumResult:
        started = time.perf_counter()
        execution = _execution(execution, mode)
        if mode == "local":
            _verify_qcloud_authentication()
        context = _problem_context(problem)
        ising = context.ising
        n_qubits = ising.num_variables
        if n_qubits < 1:
            raise ValueError("ADAPTQAOA requires at least one qubit")
        pool = self._pool(n_qubits)
        hamiltonian_terms = _pauli_terms(ising)
        gradient_terms = [
            _commutator_gradient_terms(hamiltonian_terms, operator) for operator in pool
        ]
        selected: list[PauliOperator] = []
        parameters = np.empty(0, dtype=float)
        observed_counts: dict[str, int] = defaultdict(int)
        objective_history = []
        parameter_history = []
        job_ids: list[str] = []
        qpu_metadata: list[dict[str, Any]] = []
        selection_history = []
        total_evaluations = 0
        total_iterations = 0
        last_gradient_norm = math.inf

        def build(values: Sequence[float], *, extra_gamma: float | None = None, measure=False):
            circuit_type, _, _, _ = _quantum_types()
            values = np.asarray(values, dtype=float)
            if values.ndim != 1 or len(values) != 2 * len(selected):
                raise ValueError("adaptive parameter vector does not match the selected layers")
            circuit = circuit_type(n_qubits)
            _compose_initial_state(circuit, None, n_qubits)
            for layer, operator in enumerate(selected):
                _apply_z_evolution(circuit, ising, float(values[2 * layer]))
                _apply_operator_evolution(circuit, operator, float(values[2 * layer + 1]))
            if extra_gamma is not None:
                _apply_z_evolution(circuit, ising, extra_gamma)
            if measure:
                circuit.measure_all()
            return circuit

        def energy(bitstring: str) -> float:
            return _optimization_energy(context, bitstring)

        def objective(values: np.ndarray) -> float:
            circuit = build(values, measure=True)
            raw = _run_circuit(circuit, execution, mode)
            counts = _validate_counts(dict(raw.counts or {}), n_qubits)
            for bitstring, count in counts.items():
                observed_counts[bitstring] += count
            _append_raw_metadata(raw, job_ids, qpu_metadata)
            value = _aggregate_counts(counts, energy, None)
            objective_history.append(value)
            return value

        solver_started = time.perf_counter()
        for layer in range(self.max_layers):
            gradients = []
            if mode == "local":
                gradient_circuit = build(parameters, extra_gamma=self.selection_gamma)
                raw = _run_circuit(gradient_circuit, execution, mode)
                statevector = getattr(raw, "statevector", None)
                if statevector is None:
                    raise RuntimeError(
                        "local simulator did not return a statevector for ADAPT selection"
                    )
                gradients = [
                    _statevector_expectation(statevector, terms) for terms in gradient_terms
                ]
            else:
                for terms in gradient_terms:
                    gradient, ids, metadata = _measure_pauli_terms(
                        lambda: build(parameters, extra_gamma=self.selection_gamma),
                        terms,
                        execution,
                        mode,
                        n_qubits,
                    )
                    gradients.append(gradient)
                    job_ids.extend(ids)
                    qpu_metadata.extend(metadata)
            last_gradient_norm = math.hypot(*(float(gradient) for gradient in gradients))
            if not math.isfinite(last_gradient_norm):
                raise ValueError("ADAPT-QAOA gradient norm overflowed; rescale the problem")
            best_index = max(range(len(pool)), key=lambda index: abs(gradients[index]))
            if last_gradient_norm < self.gradient_tolerance:
                break
            selection_history.append(
                {
                    "layer": layer,
                    "gradient_norm": last_gradient_norm,
                    "selected_operator": _operator_label(pool[best_index]),
                    "selected_gradient": float(gradients[best_index]),
                }
            )
            selected.append(pool[best_index])
            initial = np.concatenate(
                [parameters, np.asarray([self.selection_gamma, 0.0], dtype=float)]
            )
            parameters, _, info = _optimize(
                objective,
                initial,
                self.optimizer,
                self.max_iterations,
                seed=execution.seed,
            )
            evaluations = _optional_int(info.get("nfev"))
            iterations = _optional_int(info.get("nit"))
            if evaluations is not None:
                total_evaluations += evaluations
            if iterations is not None:
                total_iterations += iterations
            parameter_history.extend(
                np.asarray(values, dtype=float).tolist() for values in info.get("param_history", [])
            )

        final_circuit = build(parameters, measure=True)
        final_raw = _run_circuit(final_circuit, execution, mode)
        final_counts = _validate_counts(dict(final_raw.counts or {}), n_qubits)
        for bitstring, count in final_counts.items():
            observed_counts[bitstring] += count
        _append_raw_metadata(final_raw, job_ids, qpu_metadata)
        solver_elapsed = time.perf_counter() - solver_started
        samples, best, best_generic = _samples_and_best(context, observed_counts)
        diagnostics = QuantumDiagnostics(
            algorithm="ADAPTQAOA",
            backend=_execution_backend(execution, mode),
            shots=execution.shots,
            optimizer=_optimizer_name(self.optimizer),
            evaluations=total_evaluations,
            iterations=total_iterations,
            optimized_parameters=parameters.tolist(),
            objective_history=objective_history,
            parameter_history=parameter_history,
            counts=final_counts,
            candidates=samples,
            job_ids=job_ids,
            logical_circuit=_circuit_info(final_circuit),
            qpu_metadata=qpu_metadata,
            transformations=_transformation_records(context, pauli_mapping=True),
            algorithm_metadata={
                "operator_pool": self.operator_pool
                if isinstance(self.operator_pool, str)
                else "custom",
                "operator_pool_size": len(pool),
                "optimization_offset": context.optimization_offset,
                "operator_sequence": [_operator_label(operator) for operator in selected],
                "layers": len(selected),
                "selection_gamma": self.selection_gamma,
                "gradient_tolerance": self.gradient_tolerance,
                "final_gradient_norm": last_gradient_norm,
                "selection_history": selection_history,
                "candidate_distribution": "all variational and final measurement evaluations",
            },
        )
        if context.optimization_source and best is not None:
            objective_value, solution, assignment = best
            result = QuantumResult(
                problem_class=context.problem_class,
                execution_mode=mode,
                objective=objective_value,
                solution=solution,
                variable_assignment=assignment,
                quantum=diagnostics,
                solver_time_s=solver_elapsed,
                compute_time_s=time.perf_counter() - started,
            )
        else:
            result = QuantumResult(
                problem_class=context.problem_class,
                execution_mode=mode,
                eigenvalue=(best_generic if context.problem_class == "QUBIT_HAMILTONIAN" else None),
                quantum=diagnostics,
                solver_time_s=solver_elapsed,
                compute_time_s=time.perf_counter() - started,
            )
        termination = "limit_reached" if len(selected) == self.max_layers else "completed"
        return _attach_info(
            result,
            solver="ADAPTQAOA",
            method="adaptive sampled variational optimization",
            transformed=context.transformed,
            termination=termination,
            settings={"shots": execution.shots, "layers": len(selected), "pool_size": len(pool)},
        )


def _substitute_ising_relation(
    fields: dict[int, float],
    couplings: dict[tuple[int, int], float],
    offset: float,
    *,
    eliminated: int,
    retained: int,
    sign: int,
) -> tuple[dict[int, float], dict[tuple[int, int], float], float]:
    field_contributions = defaultdict(list)
    for index, coefficient in fields.items():
        if index == eliminated:
            field_contributions[retained].append(sign * coefficient)
        else:
            field_contributions[index].append(coefficient)
    coupling_contributions = defaultdict(list)
    offset_contributions = [offset]
    for (left, right), coefficient in couplings.items():
        if eliminated not in {left, right}:
            coupling_contributions[(left, right)].append(coefficient)
            continue
        other = right if left == eliminated else left
        if other == retained:
            offset_contributions.append(sign * coefficient)
        else:
            coupling_contributions[tuple(sorted((retained, other)))].append(sign * coefficient)
    try:
        updated_fields = {}
        for index, values in field_contributions.items():
            total = finite_fsum(values)
            if total != 0.0:
                updated_fields[index] = total
        updated_couplings = {}
        for pair, values in coupling_contributions.items():
            total = finite_fsum(values)
            if total != 0.0:
                updated_couplings[pair] = total
        updated_offset = finite_fsum(offset_contributions)
    except OverflowError as exc:
        raise ValueError("recursive Ising substitution overflowed") from exc
    return updated_fields, updated_couplings, updated_offset


def _reduced_ising(
    active: Sequence[int],
    fields: Mapping[int, float],
    couplings: Mapping[tuple[int, int], float],
    offset: float,
    original: _IsingData,
) -> tuple[_IsingData, dict[int, int]]:
    position = {
        original_index: reduced_index for reduced_index, original_index in enumerate(active)
    }
    reduced = _IsingData(
        linear={position[index]: value for index, value in fields.items() if index in position},
        quadratic={
            (position[left], position[right]): value
            for (left, right), value in couplings.items()
            if left in position and right in position
        },
        offset=offset,
        num_variables=len(active),
        variable_names=tuple(
            (original.variable_names[index] if original.variable_names else f"s[{index}]")
            for index in active
        ),
    )
    return reduced, position


def _is_exact_residual(problem: _IsingData) -> bool:
    metrics = _polynomial_metrics(
        _ising_polynomial(
            IsingProblem(
                linear=problem.linear,
                quadratic=problem.quadratic,
                offset=problem.offset,
                num_variables=problem.num_variables,
                variable_names=problem.variable_names,
            )
        )
    )
    return (
        metrics["active_variables"] <= NATIVE_EXACT_MAX_VARIABLES
        and metrics["exact_enumeration_work"] <= NATIVE_EXACT_MAX_WORK
    )


def _reconstruct_spins(
    active: Sequence[int],
    reduced_spins: Sequence[int],
    relations: Sequence[tuple[int, int, int]],
    num_variables: int,
) -> tuple[int, ...]:
    reconstructed = dict(zip(active, map(int, reduced_spins)))
    for eliminated, retained, sign in reversed(relations):
        reconstructed[eliminated] = sign * reconstructed[retained]
    if set(reconstructed) != set(range(num_variables)):
        raise RuntimeError("RecursiveQAOA reconstruction did not cover every source variable")
    return tuple(reconstructed[index] for index in range(num_variables))


@dataclass(frozen=True)
class RecursiveQAOA:
    """Recursive QAOA with exact Ising substitutions and exact residual solve."""

    qaoa: QAOA = field(default_factory=QAOA)
    min_variables: int = 10
    eliminations_per_step: int = 1

    def __post_init__(self):
        if not isinstance(self.qaoa, QAOA):
            raise TypeError("qaoa must be QAOA")
        _validate_positive_integer(self.min_variables, "min_variables")
        if self.min_variables > 20:
            raise ValueError("min_variables cannot exceed the local exact-solver limit of 20")
        _validate_positive_integer(self.eliminations_per_step, "eliminations_per_step")
        if (
            self.qaoa.warm_start is not None
            or self.qaoa.initial_state is not None
            or self.qaoa.mixer is not None
            or self.qaoa.initial_point is not None
        ):
            raise ValueError(
                "RecursiveQAOA requires an inner QAOA with default initial state, X mixer, and stage-local initial parameters"
            )

    def run(
        self,
        problem: Any,
        *,
        execution: QuantumRun | None = None,
        mode: str = "local",
    ) -> QuantumResult:
        started = time.perf_counter()
        execution = _execution(execution, mode)
        try:
            from ..opt_solvers._local_runtime import solve_quantum_residual
        except ImportError as exc:
            raise ImportError(_LOCAL_RUNTIME_INSTALL_ERROR) from exc

        qcloud_api_key = _verify_qcloud_authentication()
        context = _problem_context(problem)
        if not context.optimization_source:
            raise ValueError("RecursiveQAOA requires explicit optimization QUBO or Ising input")
        original = context.ising
        if original.num_variables < 1:
            raise ValueError("RecursiveQAOA requires at least one qubit")
        active = list(range(original.num_variables))
        fields = dict(original.linear)
        couplings = dict(original.quadratic)
        qubo_source = context.problem_class == "QUBO"
        offset = 0.0 if qubo_source else float(original.offset)
        relations: list[tuple[int, int, int]] = []
        stages = []
        job_ids = []
        qpu_metadata = []
        total_evaluations = 0
        total_iterations = 0
        last_diagnostics = None
        objective_history = []
        parameter_history = []
        observed_original_counts: dict[str, int] = defaultdict(int)

        solver_started = time.perf_counter()
        while True:
            reduced, position = _reduced_ising(active, fields, couplings, offset, original)
            if len(active) <= self.min_variables and _is_exact_residual(reduced):
                if mode == "platform" and not stages:
                    raise ValueError(
                        "RecursiveQAOA would perform no quantum stage for this problem; "
                        "use mode='local' or lower min_variables"
                    )
                break
            if len(active) <= 1:
                raise RuntimeError("RecursiveQAOA could not produce an exact residual")
            stage_problem = IsingProblem(
                linear=reduced.linear,
                quadratic=reduced.quadratic,
                offset=reduced.offset,
                num_variables=reduced.num_variables,
                variable_names=reduced.variable_names,
            )
            stage_scope = _verified_recursive_stage.set(self.qaoa if mode == "local" else None)
            try:
                stage_result = self.qaoa.run(stage_problem, execution=execution, mode=mode)
            finally:
                _verified_recursive_stage.reset(stage_scope)
            last_diagnostics = stage_result.quantum
            job_ids.extend(stage_result.quantum.job_ids)
            qpu_metadata.extend(stage_result.quantum.qpu_metadata)
            total_evaluations += stage_result.quantum.evaluations or 0
            total_iterations += stage_result.quantum.iterations or 0
            objective_start = len(objective_history)
            parameter_start = len(parameter_history)
            objective_history.extend(stage_result.quantum.objective_history)
            parameter_history.extend(stage_result.quantum.parameter_history)
            counts = stage_result.quantum.counts
            for sample in stage_result.quantum.candidates:
                if sample.solution is None:
                    continue
                reconstructed_sample = _reconstruct_spins(
                    active,
                    sample.solution,
                    relations,
                    original.num_variables,
                )
                bitstring = _spin_to_bitstring(reconstructed_sample)
                observed_original_counts[bitstring] += sample.count
            total = sum(counts.values())
            correlations = []
            for left_position, right_position in itertools.combinations(range(len(active)), 2):
                correlation = math.fsum(
                    count
                    * _bit_to_spin(bitstring, len(active))[left_position]
                    * _bit_to_spin(bitstring, len(active))[right_position]
                    / total
                    for bitstring, count in counts.items()
                )
                left = active[left_position]
                right = active[right_position]
                eliminated, retained = max(left, right), min(left, right)
                correlations.append(
                    (
                        abs(correlation),
                        eliminated,
                        retained,
                        1 if correlation >= 0 else -1,
                        correlation,
                    )
                )
            correlations.sort(key=lambda item: (-item[0], item[1], item[2]))
            reductions_needed = max(1, len(active) - self.min_variables)
            target = min(self.eliminations_per_step, reductions_needed, len(active) - 1)
            chosen = []
            used = set()
            for correlation in correlations:
                _, eliminated, retained, _, _ = correlation
                if eliminated in used or retained in used:
                    continue
                chosen.append(correlation)
                used.update((eliminated, retained))
                if len(chosen) == target:
                    break
            if not chosen:
                raise RuntimeError("RecursiveQAOA could not form a valid variable relation")
            stage_relations = []
            for magnitude, eliminated, retained, sign, correlation in chosen:
                fields, couplings, offset = _substitute_ising_relation(
                    fields,
                    couplings,
                    offset,
                    eliminated=eliminated,
                    retained=retained,
                    sign=sign,
                )
                if qubo_source:
                    offset = 0.0
                active.remove(eliminated)
                relations.append((eliminated, retained, sign))
                stage_relations.append(
                    {
                        "eliminated": eliminated,
                        "retained": retained,
                        "sign": sign,
                        "correlation": correlation,
                        "absolute_correlation": magnitude,
                    }
                )
            stages.append(
                {
                    "input_variables": len(position),
                    "output_variables": len(active),
                    "relations": stage_relations,
                    "objective_history_range": [objective_start, len(objective_history)],
                    "parameter_history_range": [parameter_start, len(parameter_history)],
                }
            )

        residual = reduced
        residual_problem = IsingProblem(
            linear=residual.linear,
            quadratic=residual.quadratic,
            offset=residual.offset,
            num_variables=residual.num_variables,
            variable_names=residual.variable_names,
        )
        residual_result = solve_quantum_residual(qcloud_api_key, residual_problem)
        solver_elapsed = time.perf_counter() - solver_started
        if residual_result.info().optimality_proven is not True:
            raise RuntimeError("RecursiveQAOA residual solve was not exact")
        spins = _reconstruct_spins(
            active,
            residual_result.solution,
            relations,
            original.num_variables,
        )
        residual_objective, residual_solution, residual_assignment, residual_feasible = (
            _optimization_coordinates(context, spins)
        )
        samples, _, _ = _samples_and_best(context, observed_original_counts)
        candidates = [
            (
                sample.objective if original.original_sense == "MIN" else -sample.objective,
                sample.bitstring,
                sample.objective,
                sample.solution,
                sample.variable_assignment,
            )
            for sample in samples
            if sample.feasible
        ]
        residual_bitstring = _spin_to_bitstring(spins)
        if residual_feasible:
            candidates.append(
                (
                    residual_objective if original.original_sense == "MIN" else -residual_objective,
                    residual_bitstring,
                    residual_objective,
                    residual_solution,
                    residual_assignment,
                )
            )
        if candidates:
            _, _, objective, output_solution, output_assignment = min(candidates)
        else:
            objective = None
            output_solution = None
            output_assignment = None
        diagnostics = QuantumDiagnostics(
            algorithm="RecursiveQAOA",
            backend=(
                "qpiai-opt local exact Ising" if not stages else _execution_backend(execution, mode)
            ),
            shots=execution.shots,
            optimizer=_optimizer_name(self.qaoa.optimizer) if stages else None,
            evaluations=total_evaluations,
            iterations=total_iterations,
            optimized_parameters=(
                last_diagnostics.optimized_parameters if last_diagnostics else []
            ),
            objective_history=objective_history,
            parameter_history=parameter_history,
            counts=dict(observed_original_counts),
            candidates=samples,
            job_ids=job_ids,
            logical_circuit=(last_diagnostics.logical_circuit if last_diagnostics else None),
            qpu_metadata=qpu_metadata,
            transformations=_transformation_records(context, pauli_mapping=bool(stages)),
            algorithm_metadata={
                "reductions": stages,
                "quantum_stages": len(stages),
                "quantum_execution_performed": bool(stages),
                "residual_variables": len(active),
                "residual_solver": "qpiai-opt local exact Ising",
                "residual_objective": residual_result.objective,
                "optimization_offset": original.offset if qubo_source else 0.0,
                "reconstructed_internal_energy": (
                    _qubo_energy(
                        context.problem,
                        [(1 + spin) // 2 for spin in spins],
                        context.problem.offset,
                    )
                    * (-1 if context.problem.sense == "MAX" else 1)
                    if qubo_source
                    else original.energy(spins)
                ),
                "residual_candidate": {
                    "bitstring": residual_bitstring,
                    "objective": residual_objective,
                    "feasible": residual_feasible,
                },
                "candidate_distribution": (
                    "reconstructed full-register samples from all QAOA stages"
                ),
            },
        )
        result = QuantumResult(
            problem_class=context.problem_class,
            execution_mode=mode,
            objective=objective,
            solution=output_solution,
            variable_assignment=output_assignment,
            quantum=diagnostics,
            solver_time_s=solver_elapsed,
            compute_time_s=time.perf_counter() - started,
        )
        return _attach_info(
            result,
            solver="RecursiveQAOA",
            method="recursive correlation reduction with exact residual solve",
            transformed=context.transformed,
            settings={
                "shots": execution.shots,
                "min_variables": self.min_variables,
                "eliminations_per_step": self.eliminations_per_step,
            },
        )


__all__ = ["ADAPTQAOA", "QAOA", "RecursiveQAOA", "SamplingVQE", "VQE"]
