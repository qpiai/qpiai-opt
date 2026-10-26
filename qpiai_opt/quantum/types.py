"""Validated public configuration and result types for gate-based optimization."""

from __future__ import annotations

import itertools
import math
import numbers
from collections.abc import Mapping, Sequence
from fractions import Fraction
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)
from typing_extensions import TypeAliasType

from .._numeric import is_finite_real
from ..opt_solvers.result_base import SolveInfo, TimedSolveResult

_JsonValue = TypeAliasType(
    "_JsonValue",
    None | bool | int | float | str | list["_JsonValue"] | dict[str, "_JsonValue"],
)


class _FrozenMapping(Mapping):
    __slots__ = ("_data",)

    def __init__(self, value: Mapping):
        object.__setattr__(self, "_data", MappingProxyType(dict(value)))

    def __setattr__(self, name, value):
        raise TypeError("frozen mapping does not support item assignment")

    def __delattr__(self, name):
        raise TypeError("frozen mapping does not support item deletion")

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __eq__(self, other):
        return isinstance(other, Mapping) and dict(self) == dict(other)

    def __repr__(self):
        return repr(self._data)

    def __reduce__(self):
        return type(self), (dict(self),)


def _normalize_json_value(value: Any, containers: set[int] | None = None) -> _JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return value
    if not isinstance(value, (Mapping, list, tuple)):
        raise ValueError("metadata must contain only JSON-compatible values")
    containers = set() if containers is None else containers
    identity = id(value)
    if identity in containers:
        raise ValueError("metadata cannot contain circular containers")
    containers.add(identity)
    try:
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("metadata object keys must be strings")
            return {key: _normalize_json_value(item, containers) for key, item in value.items()}
        return [_normalize_json_value(item, containers) for item in value]
    finally:
        containers.remove(identity)


class QuantumRun(BaseModel):
    """Execution settings shared by all gate-based optimization algorithms."""

    shots: int = Field(default=1024, strict=True, gt=0)
    experiment_name: str | None = None
    timeout: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    seed: int | None = Field(default=None, strict=True, ge=0, le=2**32 - 1)
    device_name: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("experiment_name", mode="before")
    @classmethod
    def validate_experiment_name(cls, value):
        if value is None:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("experiment_name must be a non-empty string or None")
        return value.strip()

    @field_validator("device_name", mode="before")
    @classmethod
    def validate_device_name(cls, value):
        if value is None:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("device_name must be a non-empty string or None")
        return value.strip()

    @field_validator("timeout", mode="before")
    @classmethod
    def validate_timeout(cls, value):
        if value is None:
            return value
        if not is_finite_real(value) or float(value) <= 0:
            raise ValueError("timeout must be a finite positive real number or None")
        return float(value)


class CVaR(BaseModel):
    """Lower-tail conditional-value-at-risk aggregation for minimization."""

    alpha: float = Field(default=1.0, gt=0, le=1, allow_inf_nan=False)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("alpha", mode="before")
    @classmethod
    def validate_alpha(cls, value):
        if not is_finite_real(value):
            raise ValueError("alpha must be a finite number in (0, 1]")
        return float(value)

    def aggregate(self, distribution: Mapping[float, float]) -> float:
        """Return the mean over the lowest-energy probability mass ``alpha``."""

        if not isinstance(distribution, Mapping) or not distribution:
            raise ValueError("cannot aggregate an empty sample distribution")
        entries = []
        for energy, probability in distribution.items():
            if not is_finite_real(energy) or not is_finite_real(probability):
                raise ValueError("sample energies and probabilities must be finite")
            probability = float(probability)
            if probability < 0:
                raise ValueError("sample probabilities must be non-negative")
            entries.append((float(energy), probability))
        try:
            mass_total = math.fsum(probability for _, probability in entries)
        except OverflowError as exc:
            raise ValueError("sample probabilities must sum to one") from exc
        if not math.isclose(mass_total, 1.0, rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("sample probabilities must sum to one")
        exact_entries = [
            (energy, Fraction.from_float(probability)) for energy, probability in entries
        ]
        exact_mass_total = sum((probability for _, probability in exact_entries), Fraction())
        tail_mass = Fraction.from_float(self.alpha) * exact_mass_total
        remaining = tail_mass
        selected = []
        positive_entries = sorted(
            (energy, probability) for energy, probability in exact_entries if probability
        )
        for position, (energy, probability) in enumerate(positive_entries):
            if probability >= remaining or position == len(positive_entries) - 1:
                selected.append((energy, remaining))
                remaining = Fraction()
                break
            selected.append((energy, probability))
            remaining -= probability
        if remaining > 0.0:
            raise ValueError("sample distribution does not contain the requested probability mass")
        if len(selected) == 1:
            return selected[0][0]
        exact = (
            sum(
                (Fraction.from_float(energy) * mass for energy, mass in selected),
                start=Fraction(),
            )
            / tail_mass
        )
        aggregate = float(exact)
        if aggregate == 0.0 and exact:
            raise ValueError("CVaR aggregate underflowed; rescale sample energies")
        return aggregate


class WarmStart(BaseModel):
    """Continuous-relaxation state and tilted-mixer settings for QAOA."""

    epsilon: float = Field(default=0.25, ge=0, le=0.5, allow_inf_nan=False)
    relaxed_solution: Mapping[Any, float] | Sequence[float] | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    @field_validator("epsilon", mode="before")
    @classmethod
    def validate_epsilon(cls, value):
        if not is_finite_real(value):
            raise ValueError("epsilon must be a finite number in [0, 0.5]")
        return float(value)

    @field_validator("relaxed_solution", mode="before")
    @classmethod
    def validate_relaxed_solution_container(cls, value):
        if value is None:
            return value
        if isinstance(value, Mapping):
            if any(
                not is_finite_real(item) or not 0 <= float(item) <= 1 for item in value.values()
            ):
                raise ValueError("relaxed_solution values must lie in [0, 1]")
            if all(
                isinstance(key, numbers.Integral) and not isinstance(key, bool) for key in value
            ):
                if set(value) != set(range(len(value))):
                    raise ValueError("relaxed_solution indices must cover [0, number of values)")
                return tuple(float(value[index]) for index in range(len(value)))
            if any(not isinstance(key, str) or not key for key in value):
                raise ValueError(
                    "relaxed_solution keys must be names or consecutive integer indices"
                )
            return {key: float(item) for key, item in value.items()}
        if isinstance(value, (str, bytes)):
            raise TypeError("relaxed_solution must be a mapping, a sequence, or None")
        try:
            values = list(value)
        except TypeError:
            raise TypeError("relaxed_solution must be a mapping, a sequence, or None") from None
        if any(not is_finite_real(item) or not 0 <= float(item) <= 1 for item in values):
            raise ValueError("relaxed_solution values must lie in [0, 1]")
        return tuple(float(item) for item in values)

    @field_validator("relaxed_solution", mode="after")
    @classmethod
    def freeze_relaxed_solution(cls, value):
        if isinstance(value, Mapping):
            return _FrozenMapping(value)
        return None if value is None else tuple(value)

    @field_serializer("relaxed_solution")
    def serialize_relaxed_solution(self, value):
        return dict(value) if isinstance(value, Mapping) else value


class XYMixer(BaseModel):
    """Product-form XY mixer preserving each configured group's Hamming weight."""

    groups: tuple[tuple[int, ...], ...]
    topology: Literal["ring", "full"] = "ring"

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("groups", mode="before")
    @classmethod
    def validate_groups(cls, value):
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise TypeError("groups must be a sequence of qubit-index sequences")
        groups = []
        occupied = set()
        for raw_group in value:
            if isinstance(raw_group, (str, bytes)) or not isinstance(raw_group, Sequence):
                raise TypeError("each XY mixer group must be a sequence")
            group = tuple(raw_group)
            if len(group) < 2:
                raise ValueError("each XY mixer group must contain at least two qubits")
            if any(isinstance(q, bool) or not isinstance(q, int) or q < 0 for q in group):
                raise ValueError("XY mixer qubits must be non-negative integers")
            if len(set(group)) != len(group):
                raise ValueError("an XY mixer group cannot repeat a qubit")
            overlap = occupied.intersection(group)
            if overlap:
                raise ValueError("XY mixer groups must be disjoint")
            occupied.update(group)
            groups.append(group)
        if not groups:
            raise ValueError("groups must contain at least one XY mixer group")
        return tuple(groups)

    @field_validator("topology", mode="before")
    @classmethod
    def normalize_topology(cls, value):
        if not isinstance(value, str):
            raise TypeError("topology must be 'ring' or 'full'")
        return value.lower()

    def edges(self) -> tuple[tuple[int, int], ...]:
        """Return the stable ordered exchange generators used by the mixer."""

        edges = []
        for group in self.groups:
            candidates = (
                zip(group, group[1:] + group[:1])
                if self.topology == "ring"
                else itertools.combinations(group, 2)
            )
            for left, right in candidates:
                pair = tuple(sorted((left, right)))
                if pair not in edges:
                    edges.append(pair)
        return tuple(edges)


class QuantumSample(BaseModel):
    """One computational-basis candidate observed during optimization."""

    bitstring: str
    count: int = Field(strict=True, ge=0)
    probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    internal_energy: float
    objective: float | None = None
    feasible: bool | None = None
    solution: list[int | float] | None = None
    variable_assignment: dict[str, int | float] | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("bitstring", mode="before")
    @classmethod
    def validate_bitstring(cls, value):
        if (
            not isinstance(value, str)
            or not value
            or any(character not in "01" for character in value)
        ):
            raise ValueError("bitstring must be a non-empty binary string")
        return value

    @field_validator("internal_energy", "objective", mode="before")
    @classmethod
    def validate_finite_values(cls, value, info):
        if value is None and info.field_name == "objective":
            return value
        if not is_finite_real(value):
            raise ValueError(f"{info.field_name} must be finite")
        return float(value)

    @field_validator("probability", mode="before")
    @classmethod
    def validate_probability(cls, value):
        if not is_finite_real(value) or not 0 <= float(value) <= 1:
            raise ValueError("probability must be a finite number in [0, 1]")
        return float(value)

    @field_validator("solution", mode="before")
    @classmethod
    def validate_solution(cls, value):
        if value is None:
            return value
        if not isinstance(value, (list, tuple)) or any(not is_finite_real(item) for item in value):
            raise ValueError("solution must contain finite numbers")
        return [int(item) if float(item).is_integer() else float(item) for item in value]

    @field_validator("variable_assignment", mode="before")
    @classmethod
    def validate_assignment(cls, value):
        if value is None:
            return value
        if not isinstance(value, Mapping) or any(
            not isinstance(name, str) or not name for name in value
        ):
            raise ValueError("variable_assignment must map non-empty names to finite numbers")
        normalized = {}
        for name, item in value.items():
            if not is_finite_real(item):
                raise ValueError("variable_assignment values must be finite")
            normalized[name] = int(item) if float(item).is_integer() else float(item)
        return normalized


class LogicalCircuitInfo(BaseModel):
    """Logical, pre-routing circuit metrics."""

    depth: int = Field(strict=True, ge=0)
    size: int = Field(strict=True, ge=0)
    gate_counts: dict[str, Annotated[int, Field(strict=True, ge=0)]]

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("gate_counts", mode="before")
    @classmethod
    def validate_gate_counts(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError("gate_counts must be a gate-name-to-count mapping")
        normalized = {}
        for name, count in value.items():
            if not isinstance(name, str) or not name:
                raise ValueError("gate_counts keys must be non-empty strings")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("gate_counts values must be non-negative integers")
            normalized[name] = count
        return normalized


class QuantumDiagnostics(BaseModel):
    """Execution and optimizer diagnostics for gate-based optimization."""

    algorithm: str
    backend: str
    shots: int = Field(strict=True, gt=0)
    optimizer: str | None = None
    evaluations: int | None = Field(default=None, strict=True, ge=0)
    iterations: int | None = Field(default=None, strict=True, ge=0)
    optimized_parameters: list[float] = Field(default_factory=list)
    objective_history: list[float] = Field(default_factory=list)
    parameter_history: list[list[float]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    candidates: list[QuantumSample] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)
    logical_circuit: LogicalCircuitInfo | None = None
    qpu_metadata: list[dict[str, _JsonValue]] = Field(default_factory=list)
    transformations: list[dict[str, _JsonValue]] = Field(default_factory=list)
    algorithm_metadata: dict[str, _JsonValue] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("algorithm", "backend", "optimizer", mode="before")
    @classmethod
    def validate_text(cls, value, info):
        if value is None and info.field_name == "optimizer":
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value.strip()

    @field_validator("optimized_parameters", "objective_history", mode="before")
    @classmethod
    def validate_finite_sequences(cls, value, info):
        if not isinstance(value, (list, tuple)) or any(not is_finite_real(item) for item in value):
            raise ValueError(f"{info.field_name} must contain finite numbers")
        return [float(item) for item in value]

    @field_validator("parameter_history", mode="before")
    @classmethod
    def validate_parameter_history(cls, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("parameter_history must be a sequence")
        normalized = []
        for parameters in value:
            if not isinstance(parameters, (list, tuple)) or any(
                not is_finite_real(item) for item in parameters
            ):
                raise ValueError("parameter_history must contain finite parameter sequences")
            normalized.append([float(item) for item in parameters])
        return normalized

    @field_validator("counts", mode="before")
    @classmethod
    def validate_counts(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError("counts must be a bitstring-to-count mapping")
        normalized = {}
        for bitstring, count in value.items():
            if (
                not isinstance(bitstring, str)
                or not bitstring
                or any(c not in "01" for c in bitstring)
            ):
                raise ValueError("counts keys must be non-empty binary strings")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("counts values must be non-negative integers")
            if count:
                normalized[bitstring] = count
        return normalized

    @field_validator("job_ids", mode="before")
    @classmethod
    def validate_job_ids(cls, value):
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError("job_ids must contain non-empty strings")
        return [item.strip() for item in value]

    @field_validator("qpu_metadata", "transformations", "algorithm_metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value):
        return _normalize_json_value(value)


class QuantumSolveInfo(SolveInfo):
    """Common interpretation of a completed quantum algorithm result."""

    problem_class: Literal["QUBO", "ISING", "QUBIT_HAMILTONIAN"]
    execution_mode: Literal["local", "platform"]
    termination: Literal["completed", "limit_reached", "failed"]
    optimality_proven: Literal[False] = False
    guarantee: Literal["best_found", "none", "unknown"]
    transformed: bool

    @model_validator(mode="after")
    def validate_semantics(self):
        if self.problem_class == "QUBIT_HAMILTONIAN" and self.has_usable_solution:
            raise ValueError(
                "a generic Hamiltonian result cannot claim a source optimization solution"
            )
        return self


class QuantumResult(TimedSolveResult):
    """Typed result for gate-based optimization algorithms."""

    problem_class: Literal["QUBO", "ISING", "QUBIT_HAMILTONIAN"]
    execution_mode: Literal["local", "platform"]
    objective: float | None = None
    variable_assignment: dict[str, int | float] | None = None
    eigenvalue: float | None = None
    quantum: QuantumDiagnostics
    solve_info: QuantumSolveInfo | None = None

    solution: list[int | float] | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("objective", "eigenvalue", mode="before")
    @classmethod
    def validate_optional_number(cls, value, info):
        if value is None:
            return value
        if not is_finite_real(value):
            raise ValueError(f"{info.field_name} must be finite")
        return float(value)

    @field_validator("solution", mode="before")
    @classmethod
    def validate_solution(cls, value):
        if value is None:
            return value
        if not isinstance(value, (list, tuple)) or any(not is_finite_real(item) for item in value):
            raise ValueError("solution must contain finite numbers")
        return [int(item) if float(item).is_integer() else float(item) for item in value]

    @field_validator("variable_assignment", mode="before")
    @classmethod
    def validate_assignment(cls, value):
        if value is None:
            return value
        if not isinstance(value, Mapping) or any(
            not isinstance(name, str) or not name for name in value
        ):
            raise ValueError("variable_assignment must map non-empty names to finite numbers")
        normalized = {}
        for name, item in value.items():
            if not is_finite_real(item):
                raise ValueError("variable_assignment values must be finite")
            normalized[name] = int(item) if float(item).is_integer() else float(item)
        return normalized

    @model_validator(mode="after")
    def validate_result_semantics(self):
        optimization = self.problem_class in {"QUBO", "ISING"}
        supplied = (self.objective, self.solution, self.variable_assignment)
        if (
            optimization
            and any(item is not None for item in supplied)
            and not all(item is not None for item in supplied)
        ):
            raise ValueError(
                "optimization objective, solution, and assignment must be supplied together"
            )
        if optimization and self.eigenvalue is not None:
            raise ValueError("optimization results cannot contain a generic eigenvalue")
        if self.problem_class == "QUBO" and self.solution is not None:
            if any(value not in (0, 1) for value in self.solution):
                raise ValueError("QUBO solution must contain only binary values")
        if self.problem_class == "ISING" and self.solution is not None:
            if any(value not in (-1, 1) for value in self.solution):
                raise ValueError("Ising solution must contain only -1 and +1 spin values")
        if self.problem_class == "QUBIT_HAMILTONIAN":
            if any(item is not None for item in supplied):
                raise ValueError(
                    "generic Hamiltonian results cannot contain a source objective or assignment"
                )
            if self.eigenvalue is None:
                raise ValueError("a generic Hamiltonian result requires an eigenvalue estimate")
        return self

    @property
    def counts(self) -> dict[str, int]:
        return self.quantum.counts

    @property
    def parameters(self) -> list[float]:
        return self.quantum.optimized_parameters

    @property
    def history(self) -> list[float]:
        return self.quantum.objective_history

    def info(self) -> QuantumSolveInfo:
        if self.solve_info is None:
            raise RuntimeError("gate-based optimization solve information is unavailable")
        return self.solve_info.model_copy(deep=True)


__all__ = [
    "CVaR",
    "LogicalCircuitInfo",
    "QuantumDiagnostics",
    "QuantumResult",
    "QuantumRun",
    "QuantumSample",
    "QuantumSolveInfo",
    "WarmStart",
    "XYMixer",
]
