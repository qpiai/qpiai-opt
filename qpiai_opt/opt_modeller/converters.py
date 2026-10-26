"""Exact converters among QUBO, HUBO, Ising, and bounded MILP forms."""

import itertools
import math
import numbers
from collections import Counter
from dataclasses import dataclass, field, replace

from .._numeric import finite_fsum, fsum_by_key, is_finite_real
from .expressions import quicksum
from .formats.hubo import calculate_hubo_energy, canonicalize_polynomial
from .formats.qubo import (
    calculate_qubo_energy,
    normalize_qubo_matrix_format,
    to_qubo_dict,
)
from .model import Model
from .utils.exceptions import InvalidQUBOError, ModelCompilationError
from .variables import Binary


def _label_key(label):
    return type(label).__name__, repr(label)


def _term_key(term):
    return tuple(sorted(set(term), key=_label_key))


def _aggregate_polynomial_terms(items):
    try:
        totals = fsum_by_key(
            (_term_key(term), coefficient) for term, coefficient in items if coefficient != 0.0
        )
    except OverflowError as exc:
        raise ModelCompilationError("HUBO coefficient aggregation overflowed") from exc
    return {term: coefficient for term, coefficient in totals.items() if coefficient != 0.0}


def _canonical_qubo(qubo):
    if not isinstance(qubo, dict):
        raise InvalidQUBOError("qubo must be a dictionary")
    contributions = []
    maximum_index = -1
    for key, coefficient in qubo.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or any(
                not isinstance(index, numbers.Integral) or isinstance(index, bool) or index < 0
                for index in key
            )
        ):
            raise InvalidQUBOError(f"Invalid QUBO key {key!r}")
        if not is_finite_real(coefficient):
            raise InvalidQUBOError(f"Invalid coefficient for QUBO key {key!r}")
        left, right = sorted(map(int, key))
        maximum_index = max(maximum_index, right)
        pair = (left, right)
        contributions.append((pair, float(coefficient)))
    try:
        canonical = fsum_by_key(contributions)
    except OverflowError as exc:
        raise InvalidQUBOError("QUBO coefficient aggregation overflowed") from exc
    return {key: value for key, value in canonical.items() if value != 0.0}, maximum_index + 1


def _validated_variable_count(num_variables, inferred):
    if num_variables is None:
        return inferred
    if (
        not isinstance(num_variables, numbers.Integral)
        or isinstance(num_variables, bool)
        or num_variables < inferred
    ):
        raise InvalidQUBOError(f"num_variables must be an integer at least {inferred}")
    return int(num_variables)


def _finite_constant(value):
    if not is_finite_real(value):
        raise InvalidQUBOError("constant must be a finite real number")
    return float(value)


def _validated_variable_names(variable_names, count, *, allow_empty=False):
    if not isinstance(variable_names, (tuple, list)):
        raise InvalidQUBOError("variable_names must be a tuple or list")
    names = tuple(variable_names)
    if allow_empty and not names:
        return names
    if (
        len(names) != count
        or any(
            not isinstance(name, str) or not name or any(char in name for char in "\x00\r\n")
            for name in names
        )
        or len(set(names)) != count
    ):
        raise InvalidQUBOError(
            "variable_names must contain one unique non-empty single-line string per variable"
        )
    return names


def _validated_indexed_sample(
    sample,
    count,
    allowed_values,
    description,
    *,
    variable_names=(),
):
    if isinstance(sample, dict):
        has_indices = all(index in sample for index in range(count))
        has_names = bool(variable_names) and all(name in sample for name in variable_names)
        if has_indices and has_names:
            indexed = [sample[index] for index in range(count)]
            named = [sample[name] for name in variable_names]
            if indexed != named:
                raise ValueError(
                    f"{description} sample contains conflicting index and variable-name values"
                )
            values = indexed
        elif has_indices:
            values = [sample[index] for index in range(count)]
        elif has_names:
            values = [sample[name] for name in variable_names]
        else:
            expected = (
                f"indices 0..{count - 1} or names {list(variable_names)!r}"
                if variable_names
                else f"indices 0..{count - 1}"
            )
            raise ValueError(f"{description} sample must contain {expected}")
    else:
        values = list(sample)
        if len(values) != count:
            raise ValueError(f"Expected {count} {description} values, got {len(values)}")
    if any(
        (allowed_values == (-1, 1) and not is_finite_real(value)) or value not in allowed_values
        for value in values
    ):
        raise ValueError(f"{description} sample contains values outside {allowed_values}")
    return [int(value) for value in values]


def _ising_energy(
    linear,
    quadratic,
    sample,
    offset,
    num_variables,
    *,
    variable_names=(),
):
    values = _validated_indexed_sample(
        sample,
        num_variables,
        (-1, 1),
        "Ising",
        variable_names=variable_names,
    )
    contributions = [float(offset)]
    contributions.extend(coefficient * values[index] for index, coefficient in linear.items())
    contributions.extend(
        coefficient * values[left] * values[right]
        for (left, right), coefficient in quadratic.items()
    )
    try:
        energy = finite_fsum(contributions)
    except OverflowError as exc:
        raise ValueError("Ising energy evaluation overflowed") from exc
    return energy


@dataclass(frozen=True)
class _IsingData:
    """Indexed Ising fields, couplings, offset, names, and source sense."""

    linear: dict
    quadratic: dict
    offset: float
    num_variables: int
    variable_names: tuple = ()
    original_sense: str = "MIN"

    def __post_init__(self):
        if (
            not isinstance(self.num_variables, numbers.Integral)
            or isinstance(self.num_variables, bool)
            or self.num_variables < 0
        ):
            raise InvalidQUBOError("num_variables must be a non-negative integer")
        count = int(self.num_variables)
        if not isinstance(self.linear, dict):
            raise InvalidQUBOError("linear Ising coefficients must be a dictionary")
        normalized_linear = {}
        for index, coefficient in self.linear.items():
            if (
                not isinstance(index, numbers.Integral)
                or isinstance(index, bool)
                or not 0 <= index < count
            ):
                raise InvalidQUBOError(f"Invalid Ising linear index {index!r}")
            if not is_finite_real(coefficient):
                raise InvalidQUBOError(f"Invalid Ising linear coefficient at {index!r}")
            if coefficient != 0.0:
                normalized_linear[int(index)] = float(coefficient)

        if not isinstance(self.quadratic, dict):
            raise InvalidQUBOError("quadratic Ising coefficients must be a dictionary")
        offset_contributions = [_finite_constant(self.offset)]
        quadratic_groups = {}
        for key, coefficient in self.quadratic.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or any(
                    not isinstance(index, numbers.Integral)
                    or isinstance(index, bool)
                    or not 0 <= index < count
                    for index in key
                )
            ):
                raise InvalidQUBOError(f"Invalid Ising quadratic key {key!r}")
            if not is_finite_real(coefficient):
                raise InvalidQUBOError(f"Invalid Ising quadratic coefficient for {key!r}")
            left, right = sorted(map(int, key))
            if left == right:
                offset_contributions.append(float(coefficient))
                continue
            pair = (left, right)
            quadratic_groups.setdefault(pair, []).append(float(coefficient))

        try:
            normalized_quadratic = {
                pair: value
                for pair, value in fsum_by_key(
                    (pair, coefficient)
                    for pair, coefficients in quadratic_groups.items()
                    for coefficient in coefficients
                ).items()
                if value != 0.0
            }
        except OverflowError as exc:
            raise InvalidQUBOError("Ising coefficient aggregation overflowed") from exc

        try:
            offset = finite_fsum(offset_contributions)
        except OverflowError as exc:
            raise InvalidQUBOError("Ising offset aggregation overflowed") from exc

        names = _validated_variable_names(
            self.variable_names,
            count,
            allow_empty=True,
        )
        if not isinstance(self.original_sense, str):
            raise InvalidQUBOError("original_sense must be MIN or MAX")
        sense = self.original_sense.upper()
        if sense not in {"MIN", "MAX"}:
            raise InvalidQUBOError("original_sense must be MIN or MAX")

        object.__setattr__(self, "linear", normalized_linear)
        object.__setattr__(self, "quadratic", normalized_quadratic)
        object.__setattr__(self, "offset", offset)
        object.__setattr__(self, "num_variables", count)
        object.__setattr__(self, "variable_names", names)
        object.__setattr__(self, "original_sense", sense)

    @property
    def index_to_variable(self):
        """Map Ising indices to variable names or indices."""

        names = self.variable_names or tuple(range(self.num_variables))
        return dict(enumerate(names))

    @property
    def variable_to_index(self):
        """Map Ising variable names or indices to indices."""

        return {variable: index for index, variable in self.index_to_variable.items()}

    def energy(self, sample):
        """Evaluate the Ising energy of a spin sample."""

        return _ising_energy(
            self.linear,
            self.quadratic,
            sample,
            self.offset,
            self.num_variables,
            variable_names=self.variable_names,
        )

    def decode(self, sample):
        """Return a validated variable-to-spin mapping."""

        values = _validated_indexed_sample(
            sample,
            self.num_variables,
            (-1, 1),
            "Ising",
            variable_names=self.variable_names,
        )
        names = self.variable_names or tuple(range(self.num_variables))
        return dict(zip(names, values))

    def source_objective(self, sample):
        """Evaluate a spin sample in the original objective sense."""

        value = self.energy(sample)
        return -value if self.original_sense == "MAX" else value

    def to_qubo(self, *, matrix_format="symmetric") -> "IsingToQUBOConvert":
        """Convert the Ising energy to QUBO using ``s = 2*x - 1``."""

        return ising_to_qubo(
            self.linear,
            self.quadratic,
            num_variables=self.num_variables,
            constant=self.offset,
            matrix_format=matrix_format,
            variable_names=self.variable_names or None,
            original_sense=self.original_sense,
        )

    def with_original_sense(self, sense):
        """Return a copy with ``MIN`` or ``MAX`` as the source sense."""

        if not isinstance(sense, str):
            raise ValueError("sense must be MIN or MAX")
        normalized = sense.upper()
        if normalized not in {"MIN", "MAX"}:
            raise ValueError("sense must be MIN or MAX")
        return replace(self, original_sense=normalized)


@dataclass(frozen=True)
class QUBOToIsingConvert(_IsingData):
    """Ising representation and decoding data from a QUBO conversion."""

    @property
    def transformations(self):
        """Describe the binary-to-spin substitution that produced the model."""

        return [
            {
                "transformation": "qubo_to_ising",
                "substitution": "x = (1 + s) / 2",
                "variable_mapping": self.index_to_variable,
                "offset": self.offset,
                "original_sense": self.original_sense,
            }
        ]


@dataclass(frozen=True)
class IsingToQUBOConvert:
    """QUBO representation and decoding data from an Ising conversion."""

    qubo: dict
    offset: float
    num_variables: int
    matrix_format: str = "symmetric"
    variable_names: tuple = ()
    original_sense: str = "MIN"

    @property
    def index_to_variable(self):
        """Map QUBO indices to source Ising names or indices."""

        names = self.variable_names or tuple(range(self.num_variables))
        return dict(enumerate(names))

    @property
    def variable_to_index(self):
        """Map source Ising names or indices to QUBO indices."""

        return {variable: index for index, variable in self.index_to_variable.items()}

    @property
    def transformations(self):
        """Describe the spin-to-binary substitution used by the conversion."""

        return [
            {
                "transformation": "ising_to_qubo",
                "substitution": "s = 2*x - 1",
                "variable_mapping": self.index_to_variable,
                "offset": self.offset,
                "original_sense": self.original_sense,
            }
        ]

    def energy(self, sample):
        """Evaluate the converted QUBO energy of a binary sample."""

        values = _validated_indexed_sample(
            sample,
            self.num_variables,
            (0, 1, False, True),
            "QUBO",
            variable_names=self.variable_names,
        )
        return calculate_qubo_energy(self.qubo, values, self.offset)

    def decode(self, sample):
        """Return a validated variable-to-binary mapping."""

        values = _validated_indexed_sample(
            sample,
            self.num_variables,
            (0, 1, False, True),
            "QUBO",
            variable_names=self.variable_names,
        )
        names = self.variable_names or tuple(range(self.num_variables))
        return dict(zip(names, values))

    def source_objective(self, sample):
        """Evaluate a binary sample in the original Ising objective sense."""

        value = self.energy(sample)
        return -value if self.original_sense == "MAX" else value


def qubo_to_ising(
    qubo,
    *,
    num_variables=None,
    constant=0.0,
    variable_names=None,
) -> QUBOToIsingConvert:
    """Convert ``E(x)`` to Ising form using ``x = (1 + s) / 2``."""

    canonical, inferred = _canonical_qubo(qubo)
    count = _validated_variable_count(num_variables, inferred)
    if variable_names is None:
        names = ()
    else:
        names = _validated_variable_names(variable_names, count)
    offset_contributions = [_finite_constant(constant)]
    linear_contributions = []
    quadratic = {}
    for (left, right), coefficient in canonical.items():
        if left == right:
            field = coefficient / 2.0
            if coefficient != 0.0 and field == 0.0:
                raise InvalidQUBOError(
                    "QUBO-to-Ising conversion underflowed a linear coefficient; rescale the model"
                )
            linear_contributions.append((left, field))
            offset_contributions.append(field)
        else:
            coupling = coefficient / 4.0
            if coefficient != 0.0 and coupling == 0.0:
                raise InvalidQUBOError(
                    "QUBO-to-Ising conversion underflowed a coupling; rescale the model"
                )
            quadratic[(left, right)] = coupling
            linear_contributions.extend(((left, coupling), (right, coupling)))
            offset_contributions.append(coupling)
    try:
        linear = fsum_by_key(linear_contributions)
        offset = finite_fsum(offset_contributions)
    except OverflowError as exc:
        raise InvalidQUBOError("QUBO-to-Ising coefficient aggregation overflowed") from exc
    return QUBOToIsingConvert(
        linear={index: coefficient for index, coefficient in linear.items() if coefficient != 0.0},
        quadratic=quadratic,
        offset=offset,
        num_variables=count,
        variable_names=names,
    )


def ising_to_qubo(
    linear,
    quadratic,
    *,
    num_variables=None,
    constant=0.0,
    matrix_format="symmetric",
    variable_names=None,
    original_sense="MIN",
) -> IsingToQUBOConvert:
    """Convert ``offset + h·s + ΣJ_ij s_i s_j`` to direct-term QUBO."""

    matrix_format = normalize_qubo_matrix_format(matrix_format)
    if isinstance(linear, dict):
        linear_items = linear.items()
    else:
        linear_items = enumerate(linear)
    normalized_linear = {}
    maximum_index = -1
    for index, coefficient in linear_items:
        if not isinstance(index, numbers.Integral) or isinstance(index, bool) or index < 0:
            raise InvalidQUBOError(f"Invalid Ising linear index {index!r}")
        if not is_finite_real(coefficient):
            raise InvalidQUBOError(f"Invalid Ising linear coefficient at {index!r}")
        index = int(index)
        maximum_index = max(maximum_index, index)
        normalized_linear[index] = float(coefficient)

    if not isinstance(quadratic, dict):
        raise InvalidQUBOError("quadratic Ising coefficients must be a dictionary")
    quadratic_contributions = []
    offset_contributions = [_finite_constant(constant)]
    for key, coefficient in quadratic.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or any(
                not isinstance(index, numbers.Integral) or isinstance(index, bool) or index < 0
                for index in key
            )
        ):
            raise InvalidQUBOError(f"Invalid Ising quadratic key {key!r}")
        if not is_finite_real(coefficient):
            raise InvalidQUBOError(f"Invalid Ising quadratic coefficient for {key!r}")
        left, right = sorted(map(int, key))
        maximum_index = max(maximum_index, right)
        if left == right:
            offset_contributions.append(float(coefficient))
        else:
            pair = (left, right)
            quadratic_contributions.append((pair, float(coefficient)))

    try:
        normalized_quadratic = fsum_by_key(quadratic_contributions)
    except OverflowError as exc:
        raise InvalidQUBOError("Ising quadratic coefficient aggregation overflowed") from exc
    normalized_quadratic = {
        pair: coefficient
        for pair, coefficient in normalized_quadratic.items()
        if coefficient != 0.0
    }

    count = _validated_variable_count(num_variables, maximum_index + 1)
    if variable_names is None:
        names = ()
    else:
        names = _validated_variable_names(variable_names, count)
    if not isinstance(original_sense, str):
        raise InvalidQUBOError("original_sense must be MIN or MAX")
    normalized_sense = original_sense.upper()
    if normalized_sense not in {"MIN", "MAX"}:
        raise InvalidQUBOError("original_sense must be MIN or MAX")
    qubo_contributions = []
    for index, coefficient in normalized_linear.items():
        qubo_contributions.append(((index, index), coefficient))
        offset_contributions.append(-coefficient)
    for (left, right), coefficient in normalized_quadratic.items():
        qubo_contributions.extend(
            (
                ((left, right), coefficient),
                ((left, left), -coefficient),
                ((right, right), -coefficient),
            )
        )
        offset_contributions.append(coefficient)
    try:
        offset = finite_fsum(offset_contributions)
        qubo = {
            key: (2.0 if key[0] == key[1] else 4.0) * coefficient
            for key, coefficient in fsum_by_key(qubo_contributions).items()
        }
        if any(not math.isfinite(coefficient) for coefficient in qubo.values()):
            raise OverflowError("scaled QUBO coefficient is not finite")
    except OverflowError as exc:
        raise InvalidQUBOError("Ising-to-QUBO coefficient aggregation overflowed") from exc
    qubo = {key: coefficient for key, coefficient in qubo.items() if coefficient != 0.0}
    keys = tuple(qubo)
    formatted_qubo = to_qubo_dict(
        [key[0] for key in keys],
        [key[1] for key in keys],
        [qubo[key] for key in keys],
        count,
        matrix_format=matrix_format,
    )
    return IsingToQUBOConvert(
        qubo=formatted_qubo,
        offset=offset,
        num_variables=count,
        matrix_format=matrix_format,
        variable_names=names,
        original_sense=normalized_sense,
    )


@dataclass(frozen=True)
class HUBOToQUBOConvert:
    """Rosenberg quadratization result with auxiliary-variable decoding data."""

    qubo: dict
    offset: float
    variable_names: tuple
    original_variables: tuple
    auxiliary_products: dict
    strength: float
    source_polynomial: dict
    original_sense: str

    @property
    def num_variables(self):
        """Return the number of variables in the quadratized QUBO."""

        return len(self.variable_names)

    @property
    def index_to_variable(self):
        """Map QUBO indices to original and auxiliary labels."""

        return dict(enumerate(self.variable_names))

    @property
    def variable_to_index(self):
        """Map original and auxiliary labels to QUBO indices."""

        return {variable: index for index, variable in self.index_to_variable.items()}

    @property
    def transformations(self):
        """Describe the Rosenberg substitutions and penalty strength."""

        return [
            {
                "transformation": "hubo_quadratization",
                "method": "shared Rosenberg products",
                "auxiliary_products": dict(self.auxiliary_products),
                "penalty_strength": self.strength,
                "original_sense": self.original_sense,
                "decode": "discard auxiliary product variables",
            }
        ]

    def _sample_by_name(self, sample, *, fill_auxiliaries):
        if isinstance(sample, dict):
            has_names = all(name in sample for name in self.original_variables)
            has_indices = all(index in sample for index in range(len(self.original_variables)))
            named_values = (
                {name: sample[name] for name in self.variable_names if name in sample}
                if has_names
                else None
            )
            indexed_values = (
                {
                    name: sample[index]
                    for index, name in enumerate(self.variable_names)
                    if index in sample
                }
                if has_indices
                else None
            )
            if named_values is not None and indexed_values is not None:
                disagree = any(
                    named_values[name] != indexed_values[name] for name in self.original_variables
                )
                if disagree:
                    raise ValueError(
                        "Integer HUBO labels make this dictionary sample ambiguous; "
                        "use a sequence or an unambiguous label-keyed mapping"
                    )
                named = indexed_values if len(indexed_values) > len(named_values) else named_values
            elif named_values is not None:
                named = named_values
            elif indexed_values is not None:
                named = indexed_values
            else:
                raise KeyError(
                    f"Sample must contain all labels {list(self.original_variables)!r} "
                    "or all corresponding QUBO indices"
                )
        else:
            values = list(sample)
            if len(values) not in {len(self.original_variables), len(self.variable_names)}:
                raise ValueError(
                    f"Expected {len(self.original_variables)} original or "
                    f"{len(self.variable_names)} full QUBO values"
                )
            named = dict(zip(self.variable_names, values))
        if any(value not in (0, 1) for value in named.values()):
            raise ValueError("Samples must contain binary values")
        named = {name: int(value) for name, value in named.items()}
        if fill_auxiliaries:
            for auxiliary, pair in self.auxiliary_products.items():
                named.setdefault(auxiliary, named[pair[0]] * named[pair[1]])
        return named

    def complete_sample(self, sample):
        """Fill auxiliary values implied by an original-variable sample."""

        named = self._sample_by_name(sample, fill_auxiliaries=True)
        return [named[name] for name in self.variable_names]

    def decode(self, sample):
        """Return only original HUBO variables from a QUBO sample."""

        named = self._sample_by_name(sample, fill_auxiliaries=True)
        return {name: named[name] for name in self.original_variables}

    def verify_auxiliaries(self, sample):
        """Return whether every supplied auxiliary satisfies its product relation."""

        named = self._sample_by_name(sample, fill_auxiliaries=False)
        missing = [name for name in self.auxiliary_products if name not in named]
        if missing:
            raise KeyError(f"Missing auxiliary variables: {missing}")
        return all(
            named[auxiliary] == named[left] * named[right]
            for auxiliary, (left, right) in self.auxiliary_products.items()
        )

    def qubo_energy(self, sample):
        """Evaluate the quadratized QUBO energy."""

        full = self.complete_sample(sample)
        return calculate_qubo_energy(self.qubo, full, self.offset)

    def original_objective(self, sample):
        """Evaluate the original HUBO polynomial."""

        decoded = self.decode(sample)
        value = calculate_hubo_energy(self.source_polynomial, decoded)
        return value


def hubo_to_qubo(
    polynomial,
    *,
    strength=None,
    sense="MIN",
    auxiliary_prefix="__qpiai_product",
    matrix_format="symmetric",
    variable_names=None,
) -> HUBOToQUBOConvert:
    """Reduce a binary polynomial to QUBO with shared Rosenberg auxiliaries."""

    matrix_format = normalize_qubo_matrix_format(matrix_format)
    source = canonicalize_polynomial(polynomial)
    if not isinstance(sense, str):
        raise ValueError("sense must be MIN or MAX")
    normalized_sense = sense.upper()
    if normalized_sense not in {"MIN", "MAX"}:
        raise ValueError("sense must be MIN or MAX")
    if not isinstance(auxiliary_prefix, str) or not auxiliary_prefix:
        raise ValueError("auxiliary_prefix must be a non-empty string")
    sign = -1.0 if normalized_sense == "MAX" else 1.0
    working = {
        term: sign * coefficient for term, coefficient in source.items() if coefficient != 0.0
    }
    active_variables = {variable for term in source for variable in term}
    if variable_names is None:
        original_variables = tuple(sorted(active_variables, key=_label_key))
    else:
        if not isinstance(variable_names, (tuple, list)):
            raise InvalidQUBOError("variable_names must be a tuple or list")
        original_variables = _validated_variable_names(variable_names, len(variable_names))
        if not active_variables <= set(original_variables):
            raise InvalidQUBOError("variable_names must cover every polynomial variable")
    used_labels = set(original_variables)
    auxiliary_products = {}
    auxiliary_order = []

    while any(len(term) > 2 for term in working):
        frequencies = Counter()
        for term in working:
            if len(term) > 2:
                frequencies.update(itertools.combinations(term, 2))
        maximum_frequency = max(frequencies.values())
        pair = min(
            (pair for pair, count in frequencies.items() if count == maximum_frequency),
            key=lambda value: (_label_key(value[0]), _label_key(value[1])),
        )
        suffix = len(auxiliary_order)
        auxiliary = f"{auxiliary_prefix}_{suffix}"
        while auxiliary in used_labels:
            suffix += 1
            auxiliary = f"{auxiliary_prefix}_{suffix}"
        used_labels.add(auxiliary)
        auxiliary_order.append(auxiliary)
        auxiliary_products[auxiliary] = pair

        reduced_contributions = []
        pair_set = set(pair)
        for term, coefficient in working.items():
            if len(term) > 2 and pair_set.issubset(term):
                new_term = [variable for variable in term if variable not in pair_set]
                new_term.append(auxiliary)
                reduced_contributions.append((new_term, coefficient))
            else:
                reduced_contributions.append((term, coefficient))
        working = _aggregate_polynomial_terms(reduced_contributions)

    try:
        objective_range = math.fsum(
            abs(coefficient) for term, coefficient in working.items() if term
        )
    except OverflowError as exc:
        raise ModelCompilationError(
            "HUBO coefficient range overflowed; rescale the polynomial"
        ) from exc
    minimum_safe = max(
        1.0,
        objective_range + max(1.0, objective_range * 1e-6),
    )
    if strength is None:
        strength = minimum_safe
    elif not is_finite_real(strength) or float(strength) < minimum_safe:
        raise ModelCompilationError(f"Quadratization strength must be at least {minimum_safe:.12g}")
    strength = float(strength)

    penalty_contributions = list(working.items())
    for auxiliary, (left, right) in auxiliary_products.items():
        penalty_contributions.extend(
            (
                ((left, right), strength),
                ((left, auxiliary), -2.0 * strength),
                ((right, auxiliary), -2.0 * strength),
                ((auxiliary,), 3.0 * strength),
            )
        )
    working = _aggregate_polynomial_terms(penalty_contributions)

    variable_names = original_variables + tuple(auxiliary_order)
    index = {name: position for position, name in enumerate(variable_names)}
    qubo_contributions = []
    offset = float(working.get((), 0.0))
    for term, coefficient in working.items():
        if not term:
            continue
        if len(term) == 1:
            key = (index[term[0]], index[term[0]])
        elif len(term) == 2:
            left, right = sorted((index[term[0]], index[term[1]]))
            key = (left, right)
        else:
            raise ModelCompilationError("HUBO reduction left a higher-order term")
        qubo_contributions.append((key, coefficient))
    try:
        qubo = fsum_by_key(qubo_contributions)
    except OverflowError as exc:
        raise ModelCompilationError("QUBO coefficient aggregation overflowed") from exc
    qubo = {key: value for key, value in qubo.items() if value != 0.0}
    keys = tuple(qubo)
    qubo = to_qubo_dict(
        [key[0] for key in keys],
        [key[1] for key in keys],
        [qubo[key] for key in keys],
        len(variable_names),
        matrix_format=matrix_format,
    )
    return HUBOToQUBOConvert(
        qubo=qubo,
        offset=offset,
        variable_names=variable_names,
        original_variables=original_variables,
        auxiliary_products=auxiliary_products,
        strength=strength,
        source_polynomial=source,
        original_sense=normalized_sense,
    )


@dataclass(frozen=True)
class HUBOToIsingConvert(_IsingData):
    """Exact quadratization and spin substitution of a binary polynomial."""

    quadratization: HUBOToQUBOConvert = field(kw_only=True)

    @property
    def transformations(self):
        return self.quadratization.transformations + [
            {
                "transformation": "qubo_to_ising",
                "substitution": "x = (1 + s) / 2",
                "variable_mapping": self.index_to_variable,
                "offset": self.offset,
                "original_sense": self.original_sense,
            }
        ]

    def decode(self, sample):
        """Discard product auxiliaries and return source binary values."""

        spins = _validated_indexed_sample(
            sample,
            self.num_variables,
            (-1, 1),
            "Ising",
            variable_names=self.variable_names,
        )
        return self.quadratization.decode([(value + 1) // 2 for value in spins])

    def source_objective(self, sample):
        return self.quadratization.original_objective(self.decode(sample))

    def complete_sample(self, sample):
        """Complete product auxiliaries and return the full spin sample."""

        return [2 * value - 1 for value in self.quadratization.complete_sample(sample)]


def hubo_to_ising(polynomial, *, sense="MIN", **options) -> HUBOToIsingConvert:
    """Quadratize exactly, then substitute x=(1+s)/2 without dropping terms."""

    quadratization = hubo_to_qubo(polynomial, sense=sense, **options)
    converted = qubo_to_ising(
        quadratization.qubo,
        constant=quadratization.offset,
        num_variables=quadratization.num_variables,
        variable_names=quadratization.variable_names,
    )
    return HUBOToIsingConvert(
        linear=converted.linear,
        quadratic=converted.quadratic,
        offset=converted.offset,
        num_variables=converted.num_variables,
        variable_names=converted.variable_names,
        original_sense=quadratization.original_sense,
        quadratization=quadratization,
    )


@dataclass(frozen=True)
class QUBOToMILPConvert:
    """MILP reformulation and source-variable map for a QUBO."""

    model: Model
    original_variables: tuple
    product_variables: dict
    constant: float

    def decode(self, solution):
        """Return the original binary assignment from a MILP solution."""

        return {variable.name: solution[variable.name] for variable in self.original_variables}

    @property
    def transformations(self):
        """Describe product-variable constraints and source mappings."""

        return [
            {
                "transformation": "qubo_to_milp",
                "product_variables": {
                    f"{left},{right}": variable.name
                    for (left, right), variable in self.product_variables.items()
                },
                "decode": "retain original binary variables",
            }
        ]


def qubo_to_milp(
    qubo,
    *,
    num_variables=None,
    variable_names=None,
    constant=0.0,
    sense="MIN",
    name="QUBO_to_MILP",
) -> QUBOToMILPConvert:
    """Create an exact binary MILP linearization of a QUBO dictionary."""

    canonical, inferred = _canonical_qubo(qubo)
    num_variables = _validated_variable_count(num_variables, inferred)
    constant = _finite_constant(constant)
    if variable_names is None:
        variable_names = [f"x_{index}" for index in range(num_variables)]
    variable_names = _validated_variable_names(variable_names, num_variables)

    variables = tuple(Binary(variable_name) for variable_name in variable_names)
    products = {}
    objective_terms = [float(constant)]
    model = Model(name)
    model.add_variables(variables)
    for (left, right), coefficient in canonical.items():
        if coefficient == 0.0:
            continue
        if left == right:
            objective_terms.append(coefficient * variables[left])
            continue
        product = Binary(f"__qpiai_product_{left}_{right}")
        product._is_auxiliary = True
        product._persist_in_ir = True
        products[(left, right)] = product
        objective_terms.append(coefficient * product)
        model.add_constraint(product <= variables[left], label=f"product_{left}_{right}_left")
        model.add_constraint(product <= variables[right], label=f"product_{left}_{right}_right")
        model.add_constraint(
            product >= variables[left] + variables[right] - 1,
            label=f"product_{left}_{right}_lower",
        )
    objective = quicksum(objective_terms)
    if not isinstance(sense, str):
        raise ValueError("sense must be MIN or MAX")
    normalized_sense = sense.upper()
    if normalized_sense == "MIN":
        model.minimize(objective)
    elif normalized_sense == "MAX":
        model.maximize(objective)
    else:
        raise ValueError("sense must be MIN or MAX")
    return QUBOToMILPConvert(model, variables, products, float(constant))


@dataclass(frozen=True)
class MILPToQUBOConvert:
    """Compiled QUBO and decoding metadata for a supported MILP model."""

    qubo: dict
    offset: float
    variable_names: tuple
    encoding_map: dict
    original_sense: str
    model: Model

    @property
    def num_variables(self):
        """Return the number of variables in the compiled QUBO."""

        return len(self.variable_names)

    @property
    def index_to_variable(self):
        """Map compiled QUBO indices to variable names."""

        return dict(enumerate(self.variable_names))

    @property
    def variable_to_index(self):
        """Map compiled variable names to QUBO indices."""

        return {variable: index for index, variable in self.index_to_variable.items()}

    def decode(self, sample):
        """Decode a compiled QUBO sample to source MILP variables."""

        return self.model.decode_qubo_sample(sample)

    @property
    def transformations(self):
        """Return compilation records emitted by the source model."""

        return self.model.transformations()


def milp_to_qubo(model, **compile_options) -> MILPToQUBOConvert:
    """Convert a supported bounded linear discrete model to QUBO.

    Continuous variables require explicit ``continuous_bits`` or
    ``continuous_precision`` in ``compile_options``.
    """

    if not isinstance(model, Model):
        raise TypeError("milp_to_qubo expects a Model")
    model._validate_linear()
    matrix_format = normalize_qubo_matrix_format(compile_options.pop("matrix_format", "symmetric"))
    rows, cols, coefficients, offset = model.to_qubo_triplets(
        matrix_format=matrix_format,
        **compile_options,
    )
    return MILPToQUBOConvert(
        qubo={
            (int(row), int(col)): float(coefficient)
            for row, col, coefficient in zip(rows, cols, coefficients)
        },
        offset=offset,
        variable_names=tuple(model.qubo_variable_names),
        encoding_map=dict(model.encoding_map),
        original_sense=model.sense,
        model=model,
    )
