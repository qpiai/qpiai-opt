"""Linear relational constraints and their exact polynomial penalties."""

import math
import numbers
from fractions import Fraction
from functools import reduce

from ..._numeric import finite_fsum, fsum_by_key
from ..expressions import Expression, _finite_product, quicksum
from ..utils.exceptions import ConstraintValidationError, EncodingError, InvalidExpressionError
from ..variables import Binary, Variable, auxiliary_name


def _coerce_expression(value):
    if isinstance(value, Expression):
        return value
    if isinstance(value, Variable):
        return value._to_expression()
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return Expression(constant=value)
    raise ConstraintValidationError(
        f"Constraint operands must be variables, expressions, or real numbers; "
        f"got {type(value).__name__}"
    )


def linear_coefficients(expression):
    """Return the variable coefficients and constant of a linear expression."""

    contributions = []
    for term in expression.terms:
        if len(term.variables) != 1:
            raise ConstraintValidationError(
                f"Only linear constraint expressions are supported, got {expression}"
            )
        contributions.append((term.variables[0], float(term.coefficient)))
    try:
        coefficients = fsum_by_key(contributions)
    except OverflowError as exc:
        raise ConstraintValidationError("Linear coefficient aggregation overflowed") from exc
    return (
        {variable: value for variable, value in coefficients.items() if value != 0.0},
        float(expression.constant),
    )


def expression_bounds(expression):
    """Return finite lower and upper bounds for a linear expression."""

    coefficients, constant = linear_coefficients(expression)
    lower_contributions = [constant]
    upper_contributions = [constant]
    for variable, coefficient in coefficients.items():
        if variable.var_type == "BINARY":
            variable_lower, variable_upper = 0.0, 1.0
        elif variable.var_type == "MIXED":
            domain = tuple(variable.valid_discrete_values) + tuple(variable.continuous_range)
            variable_lower, variable_upper = min(domain), max(domain)
        elif variable.var_type in {"SEMI_CONTINUOUS", "SEMI_INTEGER"}:
            variable_lower, variable_upper = 0.0, float(variable.upper_bound)
        elif hasattr(variable, "lower_bound") and hasattr(variable, "upper_bound"):
            variable_lower = float(variable.lower_bound)
            variable_upper = float(variable.upper_bound)
        else:
            raise ConstraintValidationError(
                f"Cannot determine bounds for variable '{variable.name}'"
            )
        if not math.isfinite(variable_lower) or not math.isfinite(variable_upper):
            raise ConstraintValidationError(
                f"Finite bounds are required to encode constraint variable '{variable.name}'"
            )
        try:
            contributions = (
                _finite_product(coefficient, variable_lower, "Linear expression bounds"),
                _finite_product(coefficient, variable_upper, "Linear expression bounds"),
            )
        except InvalidExpressionError as exc:
            raise ConstraintValidationError(str(exc)) from exc
        lower_contributions.append(min(contributions))
        upper_contributions.append(max(contributions))
    try:
        return finite_fsum(lower_contributions), finite_fsum(upper_contributions)
    except OverflowError as exc:
        raise ConstraintValidationError("Linear expression bounds overflowed") from exc


def compute_max_value(expression):
    """Return the maximum of a bounded linear expression."""

    return expression_bounds(expression)[1]


def _rational(value):
    return Fraction(str(float(value)))


def expression_lattice_step(expression, mapping=None):
    """Return the smallest represented step between expression values."""

    coefficients, constant = linear_coefficients(expression)
    rational_constant = _rational(constant)
    rational_coefficients = {}
    for variable, coefficient in coefficients.items():
        coefficient = _rational(coefficient)
        encoded = mapping.get(variable) if mapping is not None else None
        if encoded is None:
            rational_coefficients[variable] = (
                rational_coefficients.get(variable, Fraction()) + coefficient
            )
            continue

        rationals = getattr(encoded, "_encoding_rationals", None)
        if rationals is None:
            encoding = getattr(encoded, "encoding", {})
            offset = _rational(encoding.get("offset", encoded.constant))
            weighted_binaries = tuple(
                (binary, _rational(weight))
                for binary, weight in zip(
                    encoding.get("binaries", ()),
                    encoding.get("weights", ()),
                )
            )
        else:
            offset, weighted_binaries = rationals
        rational_constant += coefficient * offset
        for binary, weight in weighted_binaries:
            rational_coefficients[binary] = (
                rational_coefficients.get(binary, Fraction()) + coefficient * weight
            )

    values = [rational_constant, *rational_coefficients.values()]
    nonzero = [abs(value) for value in values if value]
    if not nonzero:
        return 1.0

    denominator = math.lcm(*(value.denominator for value in nonzero))
    integer_values = [
        abs(value.numerator * (denominator // value.denominator)) for value in nonzero
    ]
    numerator_gcd = reduce(math.gcd, integer_values)
    step = float(Fraction(numerator_gcd, denominator))
    if not math.isfinite(step) or step <= 0.0:
        raise EncodingError("Constraint lattice step is not representable; rescale the model")
    return step


def bounded_binary_weights(maximum):
    """Weights whose binary sums cover every integer in [0, maximum]."""

    maximum = int(maximum)
    if maximum < 0:
        raise EncodingError("Encoded maximum cannot be negative")
    if maximum == 0:
        return []
    bit_count = maximum.bit_length()
    if bit_count > 53:
        raise EncodingError(
            f"Encoding requires {bit_count} bits and exceeds exact binary64 integer "
            "resolution; tighten bounds or rescale the model"
        )
    weights = [1 << index for index in range(bit_count - 1)]
    represented = sum(weights)
    weights.append(maximum - represented)
    return weights


class Constraint:
    """Base class for a validated linear relation."""

    def __init__(self, lhs, rhs):
        self.lhs = _coerce_expression(lhs)
        self.rhs = _coerce_expression(rhs)
        self._penalty_cache = None
        self._validate_constraint()

    def _validate_constraint(self):
        self._validate_linear_constraint()

    @staticmethod
    def _is_linear_expression(expression):
        return all(len(term.variables) <= 1 for term in expression.terms)

    def _validate_linear_constraint(self):
        if not self._is_linear_expression(self.lhs) or not self._is_linear_expression(self.rhs):
            raise ConstraintValidationError(
                "Only linear relational constraints are supported. "
                f"Received: {self.lhs} {self._symbol()} {self.rhs}."
            )

    def _symbol(self):
        return "?"

    def to_penalty(self):
        """Return a polynomial penalty that vanishes on feasible assignments."""

        raise NotImplementedError

    def __bool__(self):
        raise TypeError("A modelling constraint has no truth value; add it to a Model instead")


class Equal(Constraint):
    """Linear equality relation."""

    def to_penalty(self):
        """Return the squared equality residual."""

        if self._penalty_cache is None:
            self._penalty_cache = (self.lhs - self.rhs) ** 2
        return self._penalty_cache

    def __repr__(self):
        return "<Constraint: Equal>"

    def _symbol(self):
        return "=="


class _Inequality(Constraint):
    _direction = 1.0
    _prefix = "slack"

    def _standard_difference(self):
        # The standard form is g(x) <= 0.
        return self._direction * (self.lhs - self.rhs)

    def to_penalty(self):
        if self._penalty_cache is not None:
            return self._penalty_cache

        difference = self._standard_difference()
        coefficients, _ = linear_coefficients(difference)
        non_binary = [variable.name for variable in coefficients if variable.var_type != "BINARY"]
        if non_binary:
            raise EncodingError(
                "Inequality penalties require binary-expanded variables; "
                f"unexpanded variables: {', '.join(sorted(non_binary))}"
            )

        minimum, maximum = expression_bounds(difference)
        if maximum <= 0.0:
            self._penalty_cache = Expression()
            return self._penalty_cache
        if minimum > 0.0:
            raise ConstraintValidationError(
                f"Constraint {self.lhs} {self._symbol()} {self.rhs} is infeasible "
                "over the declared variable bounds"
            )

        step = getattr(self, "_lattice_step", None)
        if step is None:
            step = expression_lattice_step(difference)
        maximum_steps = int(math.ceil(-minimum / step))
        weights = bounded_binary_weights(maximum_steps)
        token = getattr(self, "_aux_name_token", None)
        stem = (
            f"__qpiai_{self._prefix}_{token}"
            if token is not None
            else auxiliary_name(
                self._prefix,
                self.lhs.to_readable_string(),
                self._symbol(),
                self.rhs.to_readable_string(),
            )
        )
        auxiliaries = [Binary(f"{stem}_{index}") for index in range(len(weights))]
        for auxiliary in auxiliaries:
            auxiliary._is_auxiliary = True
        slack = quicksum(
            step * weight * auxiliary for weight, auxiliary in zip(weights, auxiliaries)
        )
        self._penalty_cache = (difference + slack) ** 2
        return self._penalty_cache


class LessEqual(_Inequality):
    """Linear less-than-or-equal relation."""

    _direction = 1.0
    _prefix = "le"

    def __repr__(self):
        return "<Constraint: LessEqual>"

    def _symbol(self):
        return "<="


class GreaterEqual(_Inequality):
    """Linear greater-than-or-equal relation."""

    _direction = -1.0
    _prefix = "ge"

    def __repr__(self):
        return "<Constraint: GreaterEqual>"

    def _symbol(self):
        return ">="
