"""Private mathematical, conversion, execution, and sampling helpers."""

from __future__ import annotations

import math
import numbers
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import combinations
from threading import Lock
from typing import Any

import numpy as np
from scipy.optimize import minimize

from .._numeric import finite_fsum, fsum_by_key, is_finite_real
from ..opt_modeller.converters import _IsingData, qubo_to_ising
from ..opt_modeller.expressions import _MAX_MULTIPLICATION_WORK
from ..opt_modeller.model import Model
from ..opt_modeller.utils.exceptions import ModelCompilationError
from ..opt_solvers.exceptions import AuthenticationError
from ..opt_solvers.hubo.models import HUBOProblem
from ..opt_solvers.ising import IsingProblem
from ..opt_solvers.qubo.models import QUBOProblem
from .types import (
    CVaR,
    LogicalCircuitInfo,
    QuantumRun,
    QuantumSample,
    WarmStart,
    _normalize_json_value,
)

_LOCAL_EXECUTION_BACKEND = "QpiAI-QSV-Local"
_DEFAULT_PLATFORM_BACKEND = "QpiAI-Indus-1"
_DEFAULT_EXPERIMENT_NAME = "Default Experiment"


def _quantum_types():
    try:
        from qpiai_quantum import Circuit, QpiAIQuantumAuth
        from qpiai_quantum.applications.matter.models import QubitHamiltonian
        from qpiai_quantum.simulator import StatevectorSimulator
    except ImportError as exc:  # pragma: no cover - depends on optional installation
        raise ImportError(
            "Gate-based optimization requires the optional 'qpiai-quantum' dependency. "
            "Install it with: pip install 'qpiai-opt[quantum]'"
        ) from exc
    return Circuit, QubitHamiltonian, QpiAIQuantumAuth, StatevectorSimulator


def _verify_qcloud_authentication() -> str:
    """Verify the active quantum-SDK credential without creating cloud work."""

    _, _, authentication, _ = _quantum_types()
    try:
        user = authentication.me()
    except Exception as exc:
        raise AuthenticationError(
            "QCloud authentication could not be verified. "
            "Register at https://qcloud.qpiai.tech/ to obtain a valid API key, "
            "then call QpiAIQuantumAuth.login(QCLOUD_API_KEY). "
            "If you already have a valid key, check connectivity and retry."
        ) from exc
    if not isinstance(user, dict) or any(
        not isinstance(user.get(field), str) or not user[field].strip()
        for field in ("name", "email", "api_key")
    ):
        raise AuthenticationError(
            "QCloud authentication returned an unverifiable user record. "
            "Register at https://qcloud.qpiai.tech/ to obtain a valid API key, "
            "then call QpiAIQuantumAuth.login(QCLOUD_API_KEY)."
        )
    return user["api_key"]


def _is_qubit_hamiltonian(value: Any) -> bool:
    try:
        _, qubit_hamiltonian, _, _ = _quantum_types()
    except ImportError:
        return False
    return isinstance(value, qubit_hamiltonian)


def _terms(hamiltonian: Any) -> list[tuple[list[tuple[int, str]], float]]:
    """Return canonical, accurately aggregated real Pauli terms."""

    if not _is_qubit_hamiltonian(hamiltonian):
        raise TypeError("Hamiltonian must be qpiai_quantum.QubitHamiltonian")
    raw_terms = hamiltonian.get_hamiltonian_terms()
    if not isinstance(raw_terms, (list, tuple)):
        raise ValueError("Hamiltonian terms must be a sequence")
    contributions = []
    for position, raw_term in enumerate(raw_terms):
        if not isinstance(raw_term, (list, tuple)) or len(raw_term) != 2:
            raise ValueError(f"Hamiltonian term {position} must be (operators, coefficient)")
        raw_ops, coefficient = raw_term
        if not isinstance(raw_ops, (list, tuple)):
            raise ValueError(f"Hamiltonian term {position} operators must be a sequence")
        if not is_finite_real(coefficient):
            raise ValueError(f"Hamiltonian coefficient at term {position} must be finite and real")
        operators = []
        seen = set()
        for operator_position, raw_operator in enumerate(raw_ops):
            if not isinstance(raw_operator, (list, tuple)) or len(raw_operator) != 2:
                raise ValueError(
                    f"Hamiltonian operator {operator_position} in term {position} must be (qubit, Pauli)"
                )
            qubit, pauli = raw_operator
            if isinstance(qubit, bool) or not isinstance(qubit, numbers.Integral) or qubit < 0:
                raise ValueError("Hamiltonian qubit indices must be non-negative integers")
            qubit = int(qubit)
            if qubit in seen:
                raise ValueError("a Hamiltonian term cannot repeat a qubit")
            seen.add(qubit)
            if not isinstance(pauli, str) or pauli.upper() not in {"I", "X", "Y", "Z"}:
                raise ValueError(f"unsupported Pauli operator {pauli!r}")
            if pauli.upper() != "I":
                operators.append((qubit, pauli.upper()))
        contributions.append((tuple(sorted(operators)), float(coefficient)))
    try:
        aggregated = fsum_by_key(contributions)
    except OverflowError as exc:
        raise ValueError("Hamiltonian coefficient aggregation overflowed") from exc
    return [
        (list(operators), coefficient)
        for operators, coefficient in sorted(
            aggregated.items(), key=lambda item: (len(item[0]), item[0])
        )
        if coefficient != 0.0
    ]


def _n_qubits(
    terms: Sequence[tuple[list[tuple[int, str]], float]],
    hamiltonian: Any | None = None,
) -> int:
    inferred = max((qubit for operators, _ in terms for qubit, _ in operators), default=-1) + 1
    if hamiltonian is not None:
        raw_inferred = (
            max(
                (
                    int(qubit)
                    for operators, _ in hamiltonian.get_hamiltonian_terms()
                    for qubit, _ in operators
                ),
                default=-1,
            )
            + 1
        )
        inferred = max(inferred, raw_inferred)
    explicit = getattr(hamiltonian, "n_qubits", None) if hamiltonian is not None else None
    if explicit is None:
        return inferred
    if (
        isinstance(explicit, bool)
        or not isinstance(explicit, numbers.Integral)
        or explicit < inferred
    ):
        raise ValueError("Hamiltonian n_qubits must be an integer covering every Pauli term")
    return int(explicit)


def _qubo_to_ising(problem: Any) -> _IsingData:
    if isinstance(problem, Mapping):
        from ..opt_solvers.routing import _normalize, classify

        problem = _normalize(problem, classify(problem))
    if isinstance(problem, _IsingData):
        return problem
    if isinstance(problem, IsingProblem):
        return problem._as_minimization()
    if isinstance(problem, QUBOProblem):
        contributions = []
        for (left, right), coefficient in problem.qubo_dict.items():
            contributions.append((tuple(sorted((int(left), int(right)))), float(coefficient)))
        try:
            direct = {
                pair: coefficient
                for pair, coefficient in fsum_by_key(contributions).items()
                if coefficient != 0.0
            }
        except OverflowError as exc:
            raise ValueError("QUBO coefficient aggregation overflowed") from exc
        if problem.sense == "MAX":
            direct = {key: -value for key, value in direct.items()}
            offset = -float(problem.offset)
        else:
            offset = float(problem.offset)
        return qubo_to_ising(
            direct,
            num_variables=problem.num_variables,
            constant=offset,
            variable_names=problem.variable_names,
        ).with_original_sense(problem.sense)
    if isinstance(problem, Model):
        detected = problem.validate()
        raise ValueError(
            f"gate-based optimization does not implicitly transform {detected} Model input; convert explicitly to QUBO or Ising first"
        )
    if _is_qubit_hamiltonian(problem):
        return convert_from_qubit_hamiltonian(problem)
    raise TypeError(
        "gate-based optimization requires explicit QUBOProblem, IsingProblem, or diagonal QubitHamiltonian input"
    )


def _pauli_terms(ising: _IsingData) -> list[tuple[list[tuple[int, str]], float]]:
    terms = []
    if ising.offset:
        terms.append(([], float(ising.offset)))
    terms.extend(
        ([(index, "Z")], -float(coefficient)) for index, coefficient in sorted(ising.linear.items())
    )
    terms.extend(
        ([(left, "Z"), (right, "Z")], float(coefficient))
        for (left, right), coefficient in sorted(ising.quadratic.items())
    )
    return terms


def convert_to_qubit_hamiltonian(problem: Any):
    """Convert QUBO/Ising input exactly under ``z = -s``."""

    _, qubit_hamiltonian, _, _ = _quantum_types()
    ising = _qubo_to_ising(problem)
    result = qubit_hamiltonian(_pauli_terms(ising))
    result.n_qubits = ising.num_variables
    result.variable_names = tuple(ising.variable_names)
    result.original_sense = ising.original_sense
    return result


def _hubo_to_qubit_hamiltonian(problem: HUBOProblem):
    """Substitute x=(1-Z)/2 exactly, retaining higher-locality Z products."""

    _, hamiltonian_type, _, _ = _quantum_types()
    names = problem.variable_names or tuple(
        sorted({name for term in problem.polynomial_dict for name in term})
    )
    indices = {name: index for index, name in enumerate(names)}
    work = sum(1 << len(term) for term in problem.polynomial_dict)
    if work > _MAX_MULTIPLICATION_WORK:
        raise ModelCompilationError(
            f"HUBO-to-Hamiltonian expansion exceeds the {_MAX_MULTIPLICATION_WORK:,} polynomial-work limit; "
            "quadratize explicitly with hubo_to_qubo() to use a two-local Hamiltonian"
        )
    sign = -1.0 if problem.sense == "MAX" else 1.0

    def contributions():
        for term, coefficient in problem.polynomial_dict.items():
            scaled = math.ldexp(sign * coefficient, -len(term))
            if coefficient != 0.0 and scaled == 0.0:
                raise ModelCompilationError(
                    "HUBO-to-Hamiltonian conversion underflowed; rescale the polynomial"
                )
            register = sorted(indices[name] for name in term)
            for size in range(len(register) + 1):
                for subset in combinations(register, size):
                    yield tuple((index, "Z") for index in subset), -scaled if size % 2 else scaled

    try:
        coefficients = fsum_by_key(contributions())
    except OverflowError as exc:
        raise ModelCompilationError(
            "HUBO-to-Hamiltonian conversion overflowed; rescale the polynomial"
        ) from exc
    hamiltonian = hamiltonian_type(
        [
            (list(operators), coefficient)
            for operators, coefficient in sorted(
                coefficients.items(), key=lambda item: (len(item[0]), item[0])
            )
            if coefficient != 0.0
        ]
    )
    hamiltonian.n_qubits = len(names)
    hamiltonian.variable_names = tuple(names)
    hamiltonian.original_sense = problem.sense
    return hamiltonian


def convert_from_qubit_hamiltonian(problem: Any) -> _IsingData:
    """Convert identity/Z/ZZ terms exactly to the SDK's Ising convention."""

    terms = _terms(problem)
    linear_contributions = []
    quadratic_contributions = []
    offsets = []
    for operators, coefficient in terms:
        if any(pauli != "Z" for _, pauli in operators):
            raise ValueError(
                "QubitHamiltonian to Ising conversion supports only identity, Z, and ZZ terms"
            )
        if not operators:
            offsets.append(coefficient)
        elif len(operators) == 1:
            linear_contributions.append((operators[0][0], -coefficient))
        elif len(operators) == 2:
            quadratic_contributions.append(
                (tuple(sorted((operators[0][0], operators[1][0]))), coefficient)
            )
        else:
            raise ValueError("Hamiltonian terms with locality above two are not Ising models")
    try:
        linear = {
            index: value
            for index, value in fsum_by_key(linear_contributions).items()
            if value != 0.0
        }
        quadratic = {
            pair: value
            for pair, value in fsum_by_key(quadratic_contributions).items()
            if value != 0.0
        }
        offset = finite_fsum(offsets) if offsets else 0.0
    except OverflowError as exc:
        raise ValueError("Hamiltonian to Ising conversion overflowed") from exc
    n_qubits = _n_qubits(terms, problem)
    names = getattr(problem, "variable_names", ())
    if names and len(names) != n_qubits:
        raise ValueError("Hamiltonian variable_names must contain one name per qubit")
    sense = getattr(problem, "original_sense", "MIN")
    return _IsingData(
        linear=linear,
        quadratic=quadratic,
        offset=offset,
        num_variables=n_qubits,
        variable_names=tuple(names),
        original_sense=sense,
    )


@dataclass(frozen=True)
class _ProblemContext:
    problem: Any
    ising: _IsingData
    problem_class: str
    optimization_source: bool
    transformed: bool
    transformations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def optimization_offset(self) -> float:
        if self.problem_class != "QUBO":
            return 0.0
        return -self.problem.offset if self.problem.sense == "MAX" else self.problem.offset


def _problem_context(problem: Any) -> _ProblemContext:
    if isinstance(problem, (Mapping, QUBOProblem, IsingProblem)):
        from ..opt_solvers.routing import _normalize, classify

        problem = _normalize(problem, classify(problem))
    if _is_qubit_hamiltonian(problem):
        return _ProblemContext(
            problem, convert_from_qubit_hamiltonian(problem), "QUBIT_HAMILTONIAN", False, True
        )
    if not isinstance(problem, (QUBOProblem, IsingProblem)):
        if isinstance(problem, Model):
            _qubo_to_ising(problem)
        raise TypeError("gate-based optimization requires QUBOProblem, IsingProblem, or QubitHamiltonian")
    ising = _qubo_to_ising(problem)
    if isinstance(problem, IsingProblem):
        problem_class = "ISING"
        transformed = problem._source_model is not None
    else:
        problem_class = "QUBO"
        transformed = True
    source_model = problem._source_model
    records = source_model.transformations() if source_model is not None else []
    for record in records:
        mapping = record.get("variable_mapping")
        if (
            record.get("transformation") in {"native_ising", "qubo_to_ising"}
            and record.get("stage") in {"representation_compilation", "representation_conversion"}
            and isinstance(mapping, dict)
            and all(isinstance(index, int) and not isinstance(index, bool) for index in mapping)
        ):
            record["variable_mapping"] = {str(index): name for index, name in mapping.items()}
    records = _normalize_json_value(records)
    return _ProblemContext(problem, ising, problem_class, True, transformed, records)


def _bit_to_spin(bitstring: str, n_qubits: int) -> tuple[int, ...]:
    if (
        not isinstance(bitstring, str)
        or len(bitstring) != n_qubits
        or any(character not in "01" for character in bitstring)
    ):
        raise ValueError("gate-based optimization returned an invalid computational-basis bitstring")
    return tuple(2 * int(character) - 1 for character in reversed(bitstring))


def _spin_to_bitstring(spins: Sequence[int]) -> str:
    values = tuple(spins)
    if not values or any(
        isinstance(value, bool)
        or not isinstance(value, numbers.Integral)
        or int(value) not in {-1, 1}
        for value in values
    ):
        raise ValueError("spin values must be a non-empty sequence in {-1, +1}")
    return "".join(str((1 + int(value)) // 2) for value in reversed(values))


def _optimization_coordinates(
    context: _ProblemContext,
    spins: Sequence[int],
) -> tuple[float, list[int], dict[str, Any], bool]:
    problem = context.problem
    spins = tuple(int(value) for value in spins)
    if isinstance(problem, IsingProblem):
        assignment = problem.decode(spins)
        feasible = (
            problem._source_model.is_feasible(assignment)
            if problem._source_model is not None
            else True
        )
        return float(problem.source_objective(spins)), list(spins), assignment, bool(feasible)
    bits = [int((1 + value) // 2) for value in spins]
    if isinstance(problem, QUBOProblem):
        source_model = getattr(problem, "_source_model", None)
        if source_model is not None:
            assignment = source_model.decode_qubo_sample(bits)
            feasible = bool(source_model.is_feasible(assignment))
            return float(source_model.evaluate_objective(assignment)), bits, assignment, feasible
        names = problem.variable_names or tuple(
            f"x[{index}]" for index in range(problem.num_variables)
        )
        return _qubo_energy(problem, bits, problem.offset), bits, dict(zip(names, bits)), True
    raise TypeError("optimization source coordinates are unavailable")


def _qubo_energy(problem: QUBOProblem, bits: Sequence[int], offset: float) -> float:
    try:
        return finite_fsum(
            [
                offset,
                *(
                    coefficient * bits[left] * bits[right]
                    for (left, right), coefficient in problem.qubo_dict.items()
                ),
            ]
        )
    except OverflowError as exc:
        raise ValueError("QUBO energy evaluation overflowed; rescale the problem") from exc


def _optimization_energy(context: _ProblemContext, bitstring: str) -> float:
    spins = _bit_to_spin(bitstring, context.ising.num_variables)
    if isinstance(context.problem, QUBOProblem):
        energy = _qubo_energy(context.problem, [(1 + spin) // 2 for spin in spins], 0.0)
        return -energy if context.problem.sense == "MAX" else energy
    return float(context.ising.energy(spins))


def _validate_counts(counts: Any, n_qubits: int) -> dict[str, int]:
    if not isinstance(counts, Mapping):
        raise RuntimeError("gate-based optimization returned no measurement-count mapping")
    normalized = {}
    for bitstring, count in counts.items():
        if (
            not isinstance(bitstring, str)
            or len(bitstring) != n_qubits
            or any(character not in "01" for character in bitstring)
        ):
            raise RuntimeError("gate-based optimization returned an invalid bitstring")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RuntimeError("gate-based optimization returned an invalid sample count")
        if count:
            normalized[bitstring] = count
    if not normalized:
        raise RuntimeError("gate-based optimization returned no measurement samples")
    return normalized


def _aggregate_counts(
    counts: Mapping[str, int],
    energy_of: Callable[[str], float],
    aggregation: CVaR | None,
) -> float:
    total = sum(counts.values())
    if total <= 0:
        raise RuntimeError("gate-based optimization returned no measurement samples")
    counts_by_energy = defaultdict(int)
    for bitstring, count in counts.items():
        energy = energy_of(bitstring)
        if not is_finite_real(energy):
            raise RuntimeError("sample energy is not finite")
        counts_by_energy[float(energy)] += count
    if aggregation is not None and aggregation.alpha != 1.0:
        distribution = {energy: count / total for energy, count in counts_by_energy.items()}
        return aggregation.aggregate(distribution)
    contributions = [energy * (count / total) for energy, count in counts_by_energy.items()]
    boundary_product = any(
        count and energy and abs(contribution) < sys.float_info.min
        for (energy, count), contribution in zip(counts_by_energy.items(), contributions)
    )
    if not boundary_product:
        try:
            aggregate = finite_fsum(contributions)
            if abs(aggregate) >= sys.float_info.min or not any(contributions):
                return aggregate
        except OverflowError:
            pass
    exact = (
        sum(
            (Fraction.from_float(energy) * count for energy, count in counts_by_energy.items()),
            start=Fraction(),
        )
        / total
    )
    aggregate = float(exact)
    if not math.isfinite(aggregate) or (aggregate == 0.0 and exact):
        raise ValueError("Sample expectation is not representable; rescale sample energies")
    return aggregate


def _samples_and_best(
    context: _ProblemContext,
    observed_counts: Mapping[str, int],
) -> tuple[list[QuantumSample], tuple[float, list[int], dict[str, Any]] | None, float | None]:
    total = sum(observed_counts.values())
    if total <= 0:
        return [], None, None
    samples = []
    feasible_candidates = []
    generic_candidates = []
    for bitstring, count in sorted(observed_counts.items()):
        spins = _bit_to_spin(bitstring, context.ising.num_variables)
        if isinstance(context.problem, QUBOProblem):
            internal_energy = _qubo_energy(
                context.problem, [(1 + spin) // 2 for spin in spins], context.problem.offset
            )
            if context.problem.sense == "MAX":
                internal_energy = -internal_energy
        else:
            internal_energy = float(context.ising.energy(spins))
        if context.optimization_source:
            objective, solution, assignment, feasible = _optimization_coordinates(context, spins)
            sample = QuantumSample(
                bitstring=bitstring,
                count=count,
                probability=count / total,
                internal_energy=internal_energy,
                objective=objective,
                feasible=feasible,
                solution=solution,
                variable_assignment=assignment,
            )
            if feasible:
                feasible_candidates.append(
                    (
                        objective if context.ising.original_sense == "MIN" else -objective,
                        bitstring,
                        objective,
                        solution,
                        assignment,
                    )
                )
        else:
            sample = QuantumSample(
                bitstring=bitstring,
                count=count,
                probability=count / total,
                internal_energy=internal_energy,
            )
            generic_candidates.append((internal_energy, bitstring))
        samples.append(sample)
    best = None
    if feasible_candidates:
        _, _, objective, solution, assignment = min(feasible_candidates)
        best = (objective, solution, assignment)
    best_generic = min(generic_candidates)[0] if generic_candidates else None
    return samples, best, best_generic


def _basis_initial_state(circuit: Any, initial_state: Sequence[int] | None, n_qubits: int) -> None:
    if initial_state is None:
        for qubit in range(n_qubits):
            circuit.h(qubit)
        return
    if isinstance(initial_state, (str, bytes)) or not isinstance(initial_state, Sequence):
        raise TypeError("initial_state must be a binary sequence or qpiai_quantum.Circuit")
    values = list(initial_state)
    if len(values) != n_qubits or any(value not in (0, 1, False, True) for value in values):
        raise ValueError("initial_state must be a binary sequence of length num_qubits")
    for qubit, value in enumerate(values):
        if int(value):
            circuit.x(qubit)


def _compose_initial_state(circuit: Any, initial_state: Any, n_qubits: int) -> None:
    circuit_type, _, _, _ = _quantum_types()
    if isinstance(initial_state, circuit_type):
        if initial_state.num_qubits != n_qubits:
            raise ValueError("initial_state circuit has the wrong number of qubits")
        if any(
            getattr(getattr(operation, "operation_type", None), "name", None) == "MEASURE"
            for operation in initial_state.icr.evolve
        ):
            raise ValueError("initial_state circuit cannot contain measurements")
        circuit.compose(initial_state)
        return
    _basis_initial_state(circuit, initial_state, n_qubits)


def _warm_start_probabilities(
    configuration: WarmStart,
    ising: _IsingData,
) -> tuple[list[float], dict[str, Any]]:
    relaxed = configuration.relaxed_solution
    metadata: dict[str, Any]
    if relaxed is None:
        relaxation_constant = 0.0
        if ising.offset:
            try:
                math.fsum(
                    abs(value)
                    for coefficients in (ising.linear, ising.quadratic)
                    for value in coefficients.values()
                )
            except OverflowError:
                relaxation_constant = ising.offset

        def box_energy(spins: np.ndarray) -> float:
            values = [relaxation_constant]
            values.extend(coefficient * spins[index] for index, coefficient in ising.linear.items())
            values.extend(
                coefficient * spins[left] * spins[right]
                for (left, right), coefficient in ising.quadratic.items()
            )
            return finite_fsum(values)

        relaxation = minimize(
            box_energy,
            np.zeros(ising.num_variables, dtype=float),
            method="L-BFGS-B",
            bounds=[(-1.0, 1.0)] * ising.num_variables,
            options={"maxiter": 200},
        )
        if not np.all(np.isfinite(relaxation.x)) or not is_finite_real(relaxation.fun):
            raise RuntimeError("continuous warm-start relaxation returned non-finite values")
        values = [(1.0 + float(value)) / 2.0 for value in relaxation.x]
        metadata = {
            "relaxation_method": "L-BFGS-B box relaxation",
            "relaxation_global": False,
            "relaxation_success": bool(relaxation.success),
            "relaxation_objective": float(relaxation.fun),
            "relaxation_offset": ising.offset - relaxation_constant,
            "relaxation_message": str(relaxation.message),
        }
    elif isinstance(relaxed, Mapping):
        names = ising.variable_names or tuple(range(ising.num_variables))
        if set(relaxed) != set(names):
            raise ValueError("warm-start relaxed_solution mapping must cover every Ising variable")
        values = [relaxed[name] for name in names]
        metadata = {"relaxation_method": "supplied", "relaxation_global": None}
    else:
        values = list(relaxed)
        if len(values) != ising.num_variables:
            raise ValueError("warm-start relaxed_solution has the wrong length")
        metadata = {"relaxation_method": "supplied", "relaxation_global": None}
    if any(not is_finite_real(value) or not 0 <= float(value) <= 1 for value in values):
        raise ValueError("warm-start relaxed_solution values must lie in [0, 1]")
    epsilon = configuration.epsilon
    clipped = [min(1.0 - epsilon, max(epsilon, float(value))) for value in values]
    metadata.update(relaxed_solution=[float(value) for value in values], clipped_solution=clipped)
    return clipped, metadata


def _rotation_angle(*factors: float) -> float:
    normalized = tuple(float(factor) for factor in factors)
    angle = math.prod(normalized)
    if math.isfinite(angle) and (angle != 0.0 or 0.0 in normalized):
        return angle
    exact = math.prod(Fraction.from_float(factor) for factor in normalized)
    try:
        angle = float(exact)
    except OverflowError as exc:
        raise ValueError("quantum rotation angle overflowed") from exc
    if not math.isfinite(angle):
        raise ValueError("quantum rotation angle overflowed")
    if angle == 0.0 and exact:
        raise ValueError("quantum rotation angle underflowed; rescale the problem")
    return angle


def _apply_z_evolution(circuit: Any, ising: _IsingData, gamma: float) -> None:
    for index, coefficient in sorted(ising.linear.items()):
        circuit.rz(index, _rotation_angle(gamma, coefficient, -2.0))
    for (left, right), coefficient in sorted(ising.quadratic.items()):
        circuit.rzz(left, right, _rotation_angle(gamma, coefficient, 2.0))


def _run_circuit(circuit: Any, execution: QuantumRun, mode: str):
    _, _, _, simulator_type = _quantum_types()
    if mode == "local":
        return simulator_type().run(
            circuit,
            shots=execution.shots,
            seed=execution.seed,
            name=execution.experiment_name,
        )
    if mode == "platform":
        kwargs: dict[str, Any] = {
            "shots": execution.shots,
            "device_name": _execution_backend(execution, mode),
            "experiment_name": _execution_experiment_name(execution),
        }
        if execution.timeout is not None:
            kwargs["timeout"] = execution.timeout
        return circuit.run(**kwargs)
    raise ValueError("mode must be 'local' or 'platform' for gate-based optimization algorithms")


def _execution_backend(execution: QuantumRun, mode: str) -> str:
    if mode == "local":
        return _LOCAL_EXECUTION_BACKEND
    if mode == "platform":
        return execution.device_name or _DEFAULT_PLATFORM_BACKEND
    raise ValueError("mode must be 'local' or 'platform' for gate-based optimization algorithms")


def _execution_experiment_name(execution: QuantumRun) -> str:
    return execution.experiment_name or _DEFAULT_EXPERIMENT_NAME


def _raw_execution_metadata(raw: Any) -> tuple[str | None, dict[str, Any] | None]:
    job_id = getattr(raw, "job_id", None)
    if job_id is not None:
        job_id = str(job_id)
    metadata = None
    for name in ("job_metadata", "qpu_metadata", "metadata"):
        candidate = getattr(raw, name, None)
        if isinstance(candidate, Mapping):
            metadata = _json_safe(dict(candidate))
            break
        if hasattr(candidate, "model_dump"):
            metadata = _json_safe(candidate.model_dump())
            break
    return job_id, metadata


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    return str(value)


def _circuit_info(circuit: Any) -> LogicalCircuitInfo:
    listing = circuit.list_gates()
    gate_counts = listing.get("gate_counts", listing)
    return LogicalCircuitInfo(
        depth=int(circuit.depth()),
        size=int(circuit.size()),
        gate_counts={str(name): int(count) for name, count in gate_counts.items()},
    )


_SPSA_RANDOM_LOCK = Lock()


@contextmanager
def _seeded_legacy_numpy(seed: int | None):
    if seed is None:
        yield
        return
    with _SPSA_RANDOM_LOCK:
        state = np.random.get_state()
        np.random.seed(seed)
        try:
            yield
        finally:
            np.random.set_state(state)


def _optimize(
    objective: Callable[[np.ndarray], float],
    initial_point: np.ndarray,
    optimizer: str | Callable,
    max_iterations: int,
    *,
    seed: int | None,
) -> tuple[np.ndarray, float, dict[str, Any]]:
    custom_optimizer = callable(optimizer)
    if custom_optimizer:
        result = optimizer(objective, initial_point.copy(), max_iterations)
        if not isinstance(result, Sequence) or len(result) not in {2, 3}:
            raise ValueError(
                "custom optimizer must return (parameters, value) or (parameters, value, info)"
            )
        try:
            raw_parameters = list(result[0])
        except TypeError:
            raise ValueError("custom optimizer parameters must be a finite real sequence") from None
        if len(raw_parameters) != len(initial_point) or any(
            not is_finite_real(item) for item in raw_parameters
        ):
            raise ValueError("custom optimizer parameters must match the finite initial vector")
        parameters = np.asarray([float(item) for item in raw_parameters], dtype=float)
        value = result[1]
        info = {} if len(result) == 2 else result[2]
        if not isinstance(info, Mapping):
            raise ValueError("custom optimizer info must be a mapping")
        info = dict(info)
        info.setdefault("history", [])
        info.setdefault("param_history", [])
        info.setdefault("nfev", None)
        info.setdefault("nit", None)
    else:
        from qpiai_quantum.algorithms.utils.optimizers import (
            cobyla_optimize,
            nelder_mead_optimize,
            powell_optimize,
            spsa_optimize,
        )

        normalized = optimizer.lower().replace("-", "_")
        functions = {
            "cobyla": cobyla_optimize,
            "nelder_mead": nelder_mead_optimize,
            "powell": powell_optimize,
            "spsa": spsa_optimize,
        }
        if normalized not in functions:
            raise ValueError(
                "optimizer must be 'cobyla', 'spsa', 'nelder_mead', 'powell', or callable"
            )
        with _seeded_legacy_numpy(seed if normalized == "spsa" else None):
            parameters, value, info = functions[normalized](
                objective=objective,
                initial_point=initial_point.copy(),
                maxiter=max_iterations,
            )
        parameters = np.asarray(parameters, dtype=float)
        info = dict(info)
    if (
        parameters.ndim != 1
        or len(parameters) != len(initial_point)
        or not np.all(np.isfinite(parameters))
    ):
        raise RuntimeError("optimizer returned an invalid parameter vector")
    if not is_finite_real(value):
        raise RuntimeError("optimizer returned a non-finite objective")
    error = ValueError if custom_optimizer else RuntimeError
    for name in ("nfev", "nit"):
        count = info.get(name)
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, numbers.Integral) or count < 0
        ):
            raise error(f"optimizer returned an invalid {name} count")
    history = info.get("history", [])
    if not isinstance(history, (list, tuple)) or any(not is_finite_real(item) for item in history):
        raise error("optimizer returned an invalid objective history")
    parameter_history = info.get("param_history", [])
    if not isinstance(parameter_history, (list, tuple)):
        raise error("optimizer returned an invalid parameter history")
    normalized_history = []
    for historical_parameters in parameter_history:
        if isinstance(historical_parameters, (str, bytes, Mapping)):
            raise error("optimizer returned an invalid parameter history")
        try:
            historical_parameters = list(historical_parameters)
        except TypeError:
            raise error("optimizer returned an invalid parameter history") from None
        if len(historical_parameters) != len(initial_point) or any(
            not is_finite_real(item) for item in historical_parameters
        ):
            raise error("optimizer returned an invalid parameter history")
        normalized_history.append([float(item) for item in historical_parameters])
    info["history"] = [float(item) for item in history]
    info["param_history"] = normalized_history
    return parameters, float(value), info


PauliString = tuple[tuple[int, str], ...]
PauliOperator = tuple[PauliString, ...]


def _apply_pauli_evolution(circuit: Any, pauli: PauliString, angle: float) -> None:
    if not pauli:
        return
    qubits = [qubit for qubit, _ in pauli]
    for qubit, operator in pauli:
        if operator == "X":
            circuit.h(qubit)
        elif operator == "Y":
            circuit.sdg(qubit)
            circuit.h(qubit)
        elif operator != "Z":
            raise ValueError(f"unsupported Pauli operator {operator!r}")
    target = qubits[-1]
    for qubit in qubits[:-1]:
        circuit.cx(qubit, target)
    circuit.rz(target, _rotation_angle(angle, 2.0))
    for qubit in reversed(qubits[:-1]):
        circuit.cx(qubit, target)
    for qubit, operator in reversed(pauli):
        if operator == "X":
            circuit.h(qubit)
        elif operator == "Y":
            circuit.h(qubit)
            circuit.s(qubit)


def _apply_operator_evolution(circuit: Any, operator: PauliOperator, angle: float) -> None:
    for pauli in operator:
        _apply_pauli_evolution(circuit, pauli, angle)


_PAULI_PRODUCT = {
    ("I", "I"): (1, "I"),
    ("I", "X"): (1, "X"),
    ("I", "Y"): (1, "Y"),
    ("I", "Z"): (1, "Z"),
    ("X", "I"): (1, "X"),
    ("Y", "I"): (1, "Y"),
    ("Z", "I"): (1, "Z"),
    ("X", "X"): (1, "I"),
    ("Y", "Y"): (1, "I"),
    ("Z", "Z"): (1, "I"),
    ("X", "Y"): (1j, "Z"),
    ("Y", "X"): (-1j, "Z"),
    ("Y", "Z"): (1j, "X"),
    ("Z", "Y"): (-1j, "X"),
    ("Z", "X"): (1j, "Y"),
    ("X", "Z"): (-1j, "Y"),
}


def _pauli_product(left: PauliString, right: PauliString) -> tuple[complex, PauliString]:
    left_map = dict(left)
    right_map = dict(right)
    phase = 1 + 0j
    product = []
    for qubit in sorted(set(left_map) | set(right_map)):
        factor, pauli = _PAULI_PRODUCT[(left_map.get(qubit, "I"), right_map.get(qubit, "I"))]
        phase *= factor
        if pauli != "I":
            product.append((qubit, pauli))
    return phase, tuple(product)


def _commutator_gradient_terms(
    hamiltonian_terms: Sequence[tuple[list[tuple[int, str]], float]],
    operator: PauliOperator,
) -> list[tuple[list[tuple[int, str]], float]]:
    contributions = []
    for hamiltonian_pauli, coefficient in hamiltonian_terms:
        hamiltonian_pauli = tuple(hamiltonian_pauli)
        for mixer_pauli in operator:
            forward_phase, product = _pauli_product(hamiltonian_pauli, mixer_pauli)
            reverse_phase, _ = _pauli_product(mixer_pauli, hamiltonian_pauli)
            gradient_coefficient = -1j * coefficient * (forward_phase - reverse_phase)
            if abs(gradient_coefficient.imag) > 1e-12:
                raise RuntimeError("internal Pauli commutator produced a non-Hermitian gradient")
            if gradient_coefficient.real:
                contributions.append((product, float(gradient_coefficient.real)))
    try:
        aggregated = fsum_by_key(contributions)
    except OverflowError as exc:
        raise ValueError("ADAPT-QAOA gradient coefficient aggregation overflowed") from exc
    return [
        (list(pauli), coefficient)
        for pauli, coefficient in sorted(aggregated.items())
        if coefficient != 0.0
    ]


def _statevector_expectation(
    statevector: Sequence[complex],
    terms: Sequence[tuple[list[tuple[int, str]], float]],
) -> float:
    try:
        state = np.asarray(statevector, dtype=complex).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("simulator returned an invalid statevector") from exc
    if state.size == 0 or state.size & (state.size - 1):
        raise RuntimeError("simulator returned an invalid statevector")
    if not np.all(np.isfinite(state.real)) or not np.all(np.isfinite(state.imag)):
        raise RuntimeError("simulator returned a non-finite statevector")
    norm = float(np.vdot(state, state).real)
    if not math.isclose(norm, 1.0, rel_tol=1e-10, abs_tol=1e-12):
        raise RuntimeError("simulator returned a non-normalized statevector")
    n_qubits = int(math.log2(state.size))
    contributions = []
    for operators, coefficient in terms:
        evolved = state.copy()
        for qubit, pauli in operators:
            shape = (2 ** (n_qubits - qubit - 1), 2, 2**qubit)
            tensor = evolved.reshape(shape)
            if pauli == "X":
                tensor = tensor[:, [1, 0], :]
            elif pauli == "Y":
                rotated = np.empty_like(tensor)
                rotated[:, 0, :] = -1j * tensor[:, 1, :]
                rotated[:, 1, :] = 1j * tensor[:, 0, :]
                tensor = rotated
            elif pauli == "Z":
                tensor[:, 1, :] *= -1
            evolved = tensor.reshape(-1)
        contributions.append(coefficient * float(np.vdot(state, evolved).real))
    return finite_fsum(contributions) if contributions else 0.0


def _group_pauli_terms(
    terms: Sequence[tuple[list[tuple[int, str]], float]],
) -> list[tuple[dict[int, str], list[tuple[list[tuple[int, str]], float]]]]:
    groups = []
    for operators, coefficient in terms:
        if not operators:
            groups.append(({}, [(operators, coefficient)]))
            continue
        for basis, group_terms in groups:
            if basis and all(
                qubit not in basis or basis[qubit] == pauli for qubit, pauli in operators
            ):
                basis.update(operators)
                group_terms.append((operators, coefficient))
                break
        else:
            groups.append((dict(operators), [(operators, coefficient)]))
    return groups


def _counts_pauli_expectation(
    counts: Mapping[str, int],
    terms: Sequence[tuple[list[tuple[int, str]], float]],
) -> float:
    total = sum(counts.values())
    if total <= 0:
        raise RuntimeError("gate-based optimization returned no measurement samples")
    contributions = []
    for operators, coefficient in terms:
        if not operators:
            contributions.append(coefficient)
            continue
        weighted = 0
        for bitstring, count in counts.items():
            spins = _bit_to_spin(bitstring, len(bitstring))
            eigenvalue = math.prod(-spins[qubit] for qubit, _ in operators)
            weighted += eigenvalue * count
        contributions.append(coefficient * (weighted / total))
    return finite_fsum(contributions) if contributions else 0.0


def _measure_pauli_terms(
    circuit_builder: Callable[[], Any],
    terms: Sequence[tuple[list[tuple[int, str]], float]],
    execution: QuantumRun,
    mode: str,
    n_qubits: int,
) -> tuple[float, list[str], list[dict[str, Any]]]:
    if not terms:
        return 0.0, [], []
    if mode == "local":
        raw = _run_circuit(circuit_builder(), execution, mode)
        statevector = getattr(raw, "statevector", None)
        if statevector is None:
            raise RuntimeError("local simulator did not return a statevector")
        return _statevector_expectation(statevector, terms), [], []
    contributions = []
    job_ids = []
    qpu_metadata = []
    for basis, group_terms in _group_pauli_terms(terms):
        if not basis:
            contributions.append(finite_fsum([coefficient for _, coefficient in group_terms]))
            continue
        circuit = circuit_builder()
        for qubit, pauli in basis.items():
            if pauli == "X":
                circuit.h(qubit)
            elif pauli == "Y":
                circuit.sdg(qubit)
                circuit.h(qubit)
        circuit.measure_all()
        raw = _run_circuit(circuit, execution, mode)
        counts = _validate_counts(dict(raw.counts or {}), n_qubits)
        contributions.append(_counts_pauli_expectation(counts, group_terms))
        job_id, metadata = _raw_execution_metadata(raw)
        if job_id:
            job_ids.append(job_id)
        if metadata:
            qpu_metadata.append(metadata)
    return finite_fsum(contributions), job_ids, qpu_metadata


__all__ = [
    "PauliOperator",
    "PauliString",
    "_aggregate_counts",
    "_apply_operator_evolution",
    "_apply_z_evolution",
    "_basis_initial_state",
    "_bit_to_spin",
    "_circuit_info",
    "_commutator_gradient_terms",
    "_compose_initial_state",
    "_is_qubit_hamiltonian",
    "_measure_pauli_terms",
    "_n_qubits",
    "_optimization_coordinates",
    "_optimize",
    "_pauli_terms",
    "_problem_context",
    "_quantum_types",
    "_qubo_to_ising",
    "_raw_execution_metadata",
    "_rotation_angle",
    "_run_circuit",
    "_samples_and_best",
    "_spin_to_bitstring",
    "_terms",
    "_validate_counts",
    "_warm_start_probabilities",
    "convert_from_qubit_hamiltonian",
    "convert_to_qubit_hamiltonian",
]
