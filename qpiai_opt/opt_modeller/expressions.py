"""Validated polynomial expressions over optimization variables."""

import math
import numbers
from collections.abc import Mapping
from copy import copy
from fractions import Fraction

import numpy as np

from .._numeric import finite_fsum, is_finite_real
from .utils.exceptions import InvalidExpressionError, NonlinearExpressionError
from .variables import Variable

_MAX_TERM_FACTORS = 100_000
_MAX_MULTIPLICATION_WORK = 100_000


def _is_real(value):
    return isinstance(value, numbers.Real) and not isinstance(value, (bool, np.bool_))


def _finite_float(value, description):
    if not _is_real(value):
        raise InvalidExpressionError(f"{description} must be a real number, got {value!r}")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise InvalidExpressionError(f"{description} must be a finite real number") from exc
    if normalized == 0.0 and value != 0:
        raise InvalidExpressionError(f"{description} is not representable as binary64")
    if not math.isfinite(normalized):
        raise InvalidExpressionError(f"{description} must be finite, got {value!r}")
    return normalized


def _finite_exact(value, description):
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise InvalidExpressionError(f"{description} overflowed; rescale the expression") from exc
    if not math.isfinite(normalized):
        raise InvalidExpressionError(f"{description} overflowed; rescale the expression")
    if normalized == 0.0 and value:
        raise InvalidExpressionError(f"{description} underflowed; rescale the expression")
    return normalized


def _finite_product(left, right, description):
    result = left * right
    if not math.isfinite(result):
        raise InvalidExpressionError(f"{description} overflowed; rescale the expression")
    if result == 0.0 and left != 0.0 and right != 0.0:
        raise InvalidExpressionError(f"{description} underflowed; rescale the expression")
    return result


def _finite_quotient(dividend, divisor, description):
    result = dividend / divisor
    if not math.isfinite(result):
        raise InvalidExpressionError(f"{description} overflowed; rescale the expression")
    if result == 0.0 and dividend != 0.0:
        raise InvalidExpressionError(f"{description} underflowed; rescale the expression")
    return result


class Term:
    """A coefficient multiplied by zero or more decision variables."""

    __slots__ = ("_coefficient", "_coefficient_contributions", "_variables")

    def __init__(self, coefficient, variables):
        normalized = _finite_float(coefficient, "Coefficient")
        try:
            raw_factors = iter(variables)
        except TypeError as exc:
            raise InvalidExpressionError("Term variables must be an iterable") from exc

        # Track only canonical factors so repeated binary and spin powers do
        # not consume memory proportional to the number of occurrences.
        binary_factors = {}
        spin_factors = {}
        other_factors = []
        for variable in raw_factors:
            if not isinstance(variable, Variable):
                raise InvalidExpressionError("Every term factor must be a decision variable")
            identity = id(variable)
            if variable.var_type == "BINARY":
                binary_factors.setdefault(identity, variable)
            elif variable.var_type == "SPIN":
                if identity in spin_factors:
                    del spin_factors[identity]
                else:
                    spin_factors[identity] = variable
            else:
                other_factors.append(variable)
            if len(other_factors) + len(binary_factors) + len(spin_factors) > _MAX_TERM_FACTORS:
                raise InvalidExpressionError(
                    "A polynomial term may not retain more than "
                    f"{_MAX_TERM_FACTORS:,} canonical factors"
                )
        factors = tuple(
            sorted(
                (*binary_factors.values(), *spin_factors.values(), *other_factors),
                key=lambda factor: (factor.name, factor.var_type, id(factor)),
            )
        )
        self._coefficient = normalized
        self._coefficient_contributions = (normalized,)
        self._variables = factors

    @classmethod
    def _from_contributions(cls, contributions, variables):
        normalized = tuple(
            value
            for contribution in contributions
            if (value := _finite_float(contribution, "Coefficient")) != 0.0
        )
        if len(normalized) == 1:
            coefficient = normalized[0]
        else:
            try:
                coefficient = finite_fsum(normalized)
            except OverflowError as exc:
                raise InvalidExpressionError(
                    "Expression coefficient aggregation overflowed; rescale the expression"
                ) from exc
        term = cls(coefficient, variables)
        term._coefficient_contributions = normalized or (0.0,)
        return term

    def _scaled(self, factor):
        return type(self)(
            _finite_product(self.coefficient, factor, "Term coefficient scaling"),
            self.variables,
        )

    def _divided(self, divisor):
        return type(self)(
            _finite_quotient(self.coefficient, divisor, "Term coefficient division"),
            self.variables,
        )

    @property
    def coefficient(self):
        """Return the finite scalar coefficient."""

        return self._coefficient

    @property
    def variables(self):
        """Return the ordered variable factors."""

        return self._variables


class Expression:
    """A polynomial expression over SDK variables.

    Binary linear and quadratic expressions use compact NumPy arrays. Higher
    order or non-binary expressions retain symbolic terms until conversion.
    """

    def __init__(self, terms=None, constant=0.0):
        self.constant = constant
        try:
            self._terms = list(terms) if terms is not None else []
        except TypeError as exc:
            raise InvalidExpressionError("Expression terms must be an iterable") from exc
        self._coeffs = np.empty(0, dtype=np.float64)
        self._var_indices = np.empty((0, 2), dtype=np.int64)
        self._variables = {}
        self._normalize_symbolic_terms()
        self._refresh_vectorized_storage()

    @classmethod
    def from_terms(cls, terms, constant=0.0):
        """Build an expression from ``Term`` objects and a constant."""

        return cls(terms=terms, constant=constant)

    @classmethod
    def _from_vectorized(
        cls,
        coeffs,
        var_indices,
        constant=0.0,
        terms=None,
        variables=None,
        constant_contributions=None,
    ):
        coeffs = np.asarray(coeffs, dtype=np.float64)
        indices = np.asarray(var_indices, dtype=np.int64)
        if coeffs.ndim != 1:
            raise InvalidExpressionError("Vectorized coefficients must be one-dimensional")
        if indices.shape != (len(coeffs), 2):
            raise InvalidExpressionError(
                f"Vectorized indices must have shape ({len(coeffs)}, 2), got {indices.shape}"
            )
        if coeffs.size and not np.all(np.isfinite(coeffs)):
            raise InvalidExpressionError("Coefficients must be finite")
        if np.any(indices < -1):
            raise InvalidExpressionError("Variable indices cannot be less than -1")

        instance = cls(constant=constant)
        if constant_contributions is not None:
            instance._set_constant_contributions(constant_contributions)
        nonzero = coeffs != 0.0
        instance._coeffs = coeffs[nonzero]
        instance._var_indices = indices[nonzero]
        instance._coeffs.setflags(write=False)
        instance._var_indices.setflags(write=False)
        instance._terms = None if terms is None else list(terms)
        instance._variables = dict(variables or {})
        return instance

    def _normalize_symbolic_terms(self):
        grouped = {}
        constant_contributions = list(self._constant_contributions)
        for raw_term in self._terms:
            term = raw_term
            if not hasattr(term, "coefficient") or not hasattr(term, "variables"):
                raise InvalidExpressionError("terms must contain Term-compatible objects")
            coefficient = _finite_float(term.coefficient, "Coefficient")
            contributions = getattr(term, "_coefficient_contributions", (coefficient,))
            variables = list(term.variables)
            if any(not isinstance(variable, Variable) for variable in variables):
                raise InvalidExpressionError("Every term factor must be a decision variable")
            if coefficient == 0.0:
                continue
            if not variables:
                constant_contributions.extend(contributions)
                continue
            normalized_term = Term(coefficient, variables)
            if not normalized_term.variables:
                constant_contributions.extend(contributions)
                continue
            key = tuple(id(variable) for variable in normalized_term.variables)
            if key not in grouped:
                grouped[key] = [normalized_term.variables, []]
            grouped[key][1].extend(contributions)

        normalized = []
        for variables, coefficients in grouped.values():
            term = Term._from_contributions(coefficients, variables)
            if term.coefficient != 0.0:
                normalized.append(term)
        self._terms = normalized
        self._set_constant_contributions(constant_contributions)

    def _set_constant_contributions(self, values):
        normalized = tuple(
            value
            for contribution in values
            if (value := _finite_float(contribution, "Constant")) != 0.0
        )
        if len(normalized) == 1:
            total = normalized[0]
        else:
            try:
                total = finite_fsum(normalized)
            except OverflowError as exc:
                raise InvalidExpressionError(
                    "Expression constant aggregation overflowed; rescale the expression"
                ) from exc
        self._constant_contributions = normalized or (0.0,)
        self._constant = total

    @staticmethod
    def _binary_term_indices(term):
        indices = []
        for variable in term.variables:
            if variable.index is None or variable.var_type != "BINARY":
                return None
            indices.append(int(variable.index))
        return sorted(set(indices))

    @classmethod
    def _term_degree(cls, term):
        binary_indices = cls._binary_term_indices(term)
        return len(binary_indices) if binary_indices is not None else len(term.variables)

    def _can_vectorize(self):
        if self._terms is None:
            return True
        return all(
            (indices := self._binary_term_indices(term)) is not None and len(indices) <= 2
            for term in self._terms
        )

    def _refresh_vectorized_storage(self):
        if self._terms is None:
            return
        self._variables = {
            variable.index: variable
            for term in self._terms
            for variable in term.variables
            if variable.index is not None
        }
        if not self._terms or not self._can_vectorize():
            self._coeffs = np.empty(0, dtype=np.float64)
            self._var_indices = np.empty((0, 2), dtype=np.int64)
            self._coeffs.setflags(write=False)
            self._var_indices.setflags(write=False)
            return

        if all(len(term._coefficient_contributions) == 1 for term in self._terms):
            coeffs = np.fromiter(
                (term.coefficient for term in self._terms),
                dtype=np.float64,
                count=len(self._terms),
            )
            indices = np.full((len(self._terms), 2), -1, dtype=np.int64)
            positioned_terms = enumerate(self._terms)
        else:
            stored_terms = [
                (coefficient, term)
                for term in self._terms
                for coefficient in term._coefficient_contributions
            ]
            coeffs = np.fromiter(
                (coefficient for coefficient, _ in stored_terms),
                dtype=np.float64,
                count=len(stored_terms),
            )
            indices = np.full((len(stored_terms), 2), -1, dtype=np.int64)
            positioned_terms = ((position, term) for position, (_, term) in enumerate(stored_terms))
        for position, term in positioned_terms:
            binary_indices = self._binary_term_indices(term)
            indices[position, : len(binary_indices)] = binary_indices
        self._coeffs = coeffs
        self._var_indices = indices
        self._coeffs.setflags(write=False)
        self._var_indices.setflags(write=False)

    def to_binary_quadratic_arrays(self):
        """Return detached coefficient/index arrays and the constant.

        Indices identify variables by ``Variable.index``; -1 denotes an absent
        factor. Only binary expressions of degree at most two are representable.
        """

        if not self._can_vectorize():
            raise InvalidExpressionError(
                "Binary quadratic arrays are unavailable for higher-order or nonbinary "
                "expressions; use to_terms() and .constant"
            )
        return self._coeffs.copy(), self._var_indices.copy(), self.constant

    def to_terms(self):
        """Return a copy of the expression's symbolic terms."""

        if self._terms is None:
            terms = []
            for coefficient, indices in zip(self._coeffs, self._var_indices):
                variables = []
                for index in indices:
                    if index < 0:
                        continue
                    variable = self._variables.get(int(index))
                    if variable is None:
                        raise InvalidExpressionError(
                            f"Binary variable identity {int(index)} is no longer available"
                        )
                    variables.append(variable)
                terms.append(Term(coefficient, variables))
            self._terms = terms
        return list(self._terms)

    def _grouped_terms(self):
        grouped = {}
        for term in self.to_terms():
            key = tuple(id(variable) for variable in term.variables)
            if key not in grouped:
                grouped[key] = [term.variables, []]
            grouped[key][1].extend(term._coefficient_contributions)
        terms = []
        for variables, contributions in grouped.values():
            term = Term._from_contributions(contributions, variables)
            if term.coefficient != 0.0:
                terms.append(term)
        return terms

    def degree(self, raise_on_higher_order=False):
        """Return the polynomial degree."""

        if not isinstance(raise_on_higher_order, bool):
            raise TypeError("raise_on_higher_order must be a boolean")
        max_degree = 0
        if self._terms is not None:
            max_degree = max((self._term_degree(term) for term in self._terms), default=0)
        elif self._var_indices.size:
            first = self._var_indices[:, 0]
            second = self._var_indices[:, 1]
            if np.any((first >= 0) & (second >= 0) & (first != second)):
                max_degree = 2
            elif np.any(first >= 0):
                max_degree = 1

        if raise_on_higher_order and max_degree > 2:
            raise NonlinearExpressionError(
                f"Polynomial degree {max_degree} exceeds the quadratic limit"
            )
        return max_degree

    def __add__(self, other):
        if _is_real(other):
            scalar = _finite_float(other, "Expression addend")
            constant_contributions = (*self._constant_contributions, scalar)
            return (
                Expression._from_vectorized(
                    self._coeffs.copy(),
                    self._var_indices.copy(),
                    constant_contributions=constant_contributions,
                    terms=None if self._terms is None else self._terms,
                    variables=self._variables,
                )
                if self._can_vectorize()
                else self._symbolic_with_constants(self.to_terms(), constant_contributions)
            )
        if isinstance(other, Variable):
            other = other._to_expression()
        if isinstance(other, Expression):
            constant_contributions = (
                *self._constant_contributions,
                *other._constant_contributions,
            )
            if self._can_vectorize() and other._can_vectorize():
                return Expression._from_vectorized(
                    np.concatenate((self._coeffs, other._coeffs)),
                    np.concatenate((self._var_indices, other._var_indices), axis=0),
                    constant_contributions=constant_contributions,
                    variables={**self._variables, **other._variables},
                )
            return self._symbolic_with_constants(
                self.to_terms() + other.to_terms(),
                constant_contributions,
            )
        return NotImplemented

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        result = (
            other * -1
            if isinstance(other, (Variable, Expression)) or _is_real(other)
            else NotImplemented
        )
        if result is NotImplemented:
            return NotImplemented
        return self + result

    def __rsub__(self, other):
        return (-self) + other

    def __mul__(self, other):
        if _is_real(other):
            factor = _finite_float(other, "Expression multiplier")
            return Expression(
                terms=[term._scaled(factor) for term in self._grouped_terms()],
                constant=_finite_product(
                    self.constant,
                    factor,
                    "Expression constant scaling",
                ),
            )
        if isinstance(other, Variable):
            other = other._to_expression()
        if isinstance(other, Expression):
            left_terms = self._grouped_terms()
            right_terms = other._grouped_terms()
            left_constant = self.constant
            right_constant = other.constant
            left_count = len(left_terms)
            right_count = len(right_terms)
            left_factors = sum(len(term.variables) for term in left_terms)
            right_factors = sum(len(term.variables) for term in right_terms)
            projected_work = (
                left_count * right_count + right_count * left_factors + left_count * right_factors
            )
            if right_constant:
                projected_work += left_count + left_factors
            if left_constant:
                projected_work += right_count + right_factors
            if projected_work > _MAX_MULTIPLICATION_WORK:
                raise InvalidExpressionError(
                    "Expression multiplication would require too much intermediate work; "
                    "build the polynomial from explicit terms instead"
                )
            grouped_products = {}

            def add_product(variables, left, right):
                canonical_variables = Term(1.0, variables).variables
                key = tuple(id(variable) for variable in canonical_variables)
                if key not in grouped_products:
                    grouped_products[key] = [canonical_variables, []]
                grouped_products[key][1].append((left, right))

            add_product((), left_constant, right_constant)
            for left in left_terms:
                for right in right_terms:
                    add_product(
                        left.variables + right.variables,
                        left.coefficient,
                        right.coefficient,
                    )
                if right_constant:
                    add_product(left.variables, left.coefficient, right_constant)
            if left_constant:
                for right in right_terms:
                    add_product(
                        right.variables,
                        left_constant,
                        right.coefficient,
                    )

            result_terms = []
            result_constant = 0.0
            for variables, products in grouped_products.values():
                if len(products) == 1:
                    coefficient = _finite_product(
                        *products[0],
                        "Expression coefficient multiplication",
                    )
                else:
                    coefficient = _finite_exact(
                        sum(
                            (
                                Fraction.from_float(left) * Fraction.from_float(right)
                                for left, right in products
                            ),
                            start=Fraction(),
                        ),
                        "Expression coefficient multiplication",
                    )
                if coefficient == 0.0:
                    continue
                if variables:
                    result_terms.append(Term(coefficient, variables))
                else:
                    result_constant = coefficient
            return Expression(terms=result_terms, constant=result_constant)
        return NotImplemented

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        if not _is_real(other):
            raise InvalidExpressionError(
                "Polynomial expressions can only be divided by a finite real scalar"
            )
        try:
            divisor = float(other)
        except (OverflowError, TypeError, ValueError) as exc:
            raise InvalidExpressionError("Expression divisor must be a finite real scalar") from exc
        if divisor == 0.0:
            raise ZeroDivisionError("division by zero")
        if not math.isfinite(divisor):
            raise InvalidExpressionError("Expression divisor must be finite")
        return Expression(
            terms=[term._divided(divisor) for term in self._grouped_terms()],
            constant=_finite_quotient(
                self.constant,
                divisor,
                "Expression constant division",
            ),
        )

    def __rtruediv__(self, other):
        raise InvalidExpressionError(
            "Division by a polynomial expression is not supported; use a finite scalar divisor"
        )

    def __neg__(self):
        return self * -1.0

    def __pos__(self):
        return self

    @classmethod
    def _symbolic_with_constants(cls, terms, constant_contributions):
        explicit_constants = tuple(constant_contributions)
        result = cls(terms=terms)
        result._set_constant_contributions((*explicit_constants, *result._constant_contributions))
        return result

    def __pow__(self, power):
        if (
            not isinstance(power, numbers.Integral)
            or isinstance(power, (bool, np.bool_))
            or power < 0
        ):
            raise InvalidExpressionError(f"Invalid power {power!r}. Use a non-negative integer.")
        power = int(power)
        if power == 0:
            return Expression(constant=1.0)
        if power == 1:
            return self
        result = Expression(constant=1.0)
        factor = self
        while power:
            if power & 1:
                result = result * factor
            power >>= 1
            if power:
                factor = factor * factor
        return result

    def __rpow__(self, base):
        raise InvalidExpressionError(
            "Expression-valued exponents are not supported. Polynomial powers place the "
            "non-negative integer on the right, for example (x + y) ** 2."
        )

    def __le__(self, other):
        from .constraints.relational import LessEqual

        return LessEqual(self, other)

    def __ge__(self, other):
        from .constraints.relational import GreaterEqual

        return GreaterEqual(self, other)

    def __eq__(self, other):
        from .constraints.relational import Equal

        return Equal(self, other)

    def evaluate(self, values):
        """Evaluate the polynomial for a mapping keyed by variable or name."""

        if not isinstance(values, Mapping):
            raise InvalidExpressionError(
                "Expression values must be a mapping keyed by variable objects or names"
            )
        contributions = [self.constant]
        for term in self.to_terms():
            product = term.coefficient
            for variable in term.variables:
                if variable in values:
                    value = values[variable]
                elif variable.name in values:
                    value = values[variable.name]
                else:
                    raise KeyError(f"Missing value for variable '{variable.name}'")
                if not is_finite_real(value, allow_bool=True):
                    raise InvalidExpressionError(
                        f"Value for variable '{variable.name}' must be a finite real number"
                    )
                product = _finite_product(product, float(value), "Expression evaluation")
            contributions.append(product)
        try:
            result = finite_fsum(contributions)
        except OverflowError as exc:
            raise InvalidExpressionError("Expression evaluation overflowed") from exc
        return float(result)

    def __repr__(self):
        count = len(self._terms) if self._terms is not None else len(self._coeffs)
        return f"<Expression: {count} terms, constant={self.constant}>"

    def __str__(self):
        return self.to_readable_string()

    def to_readable_string(self):
        """Return a deterministic algebraic representation."""

        parts = [
            formatted
            for term in self.to_terms()
            if (formatted := self._format_term(term.coefficient, term.variables)) is not None
        ]
        if self.constant != 0.0 or not parts:
            parts.append(self._format_number(self.constant))

        expression = parts[0]
        for part in parts[1:]:
            expression += f" - {part[1:]}" if part.startswith("-") else f" + {part}"
        return expression

    @staticmethod
    def _format_number(value):
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:.12g}"

    @classmethod
    def _format_term(cls, coefficient, variables):
        coefficient = float(coefficient)
        if coefficient == 0.0:
            return None
        if not variables:
            return cls._format_number(coefficient)
        body = "*".join(variable.name for variable in variables)
        magnitude = abs(coefficient)
        if magnitude != 1.0:
            body = f"{cls._format_number(magnitude)}*{body}"
        return f"-{body}" if coefficient < 0 else body

    @property
    def terms(self):
        """Return normalized symbolic terms."""

        return self.to_terms()

    @terms.setter
    def terms(self, value):
        """Replace and normalize the symbolic terms."""

        if not isinstance(value, list):
            raise ValueError("terms must be a list of Term objects")
        replacement = copy(self)
        replacement._terms = list(value)
        replacement._normalize_symbolic_terms()
        replacement._refresh_vectorized_storage()
        self.__dict__.update(replacement.__dict__)

    @property
    def constant(self):
        """Return the expression constant."""

        return self._constant

    @constant.setter
    def constant(self, value):
        """Set a finite real expression constant."""

        normalized = _finite_float(value, "Constant")
        self._constant = normalized
        self._constant_contributions = (normalized,)


def quicksum(items):
    """Build a sum in one allocation instead of repeated array concatenation."""

    try:
        items = iter(items)
    except TypeError as exc:
        raise InvalidExpressionError("quicksum expects an iterable") from exc
    expressions = []
    constant_contributions = []
    for item in items:
        if _is_real(item):
            constant_contributions.append(_finite_float(item, "quicksum item"))
        elif isinstance(item, Variable):
            expressions.append(item._to_expression())
        elif isinstance(item, Expression):
            expressions.append(item)
        else:
            raise InvalidExpressionError(
                f"quicksum expected variables, expressions, or real numbers; got {type(item).__name__}"
            )

    constant_contributions.extend(
        contribution
        for expression in expressions
        for contribution in expression._constant_contributions
    )
    if not expressions:
        expression = Expression()
        expression._set_constant_contributions(constant_contributions)
        return expression
    if all(expression._can_vectorize() for expression in expressions):
        return Expression._from_vectorized(
            np.concatenate([expression._coeffs for expression in expressions]),
            np.concatenate([expression._var_indices for expression in expressions], axis=0),
            constant_contributions=constant_contributions,
            variables={
                index: variable
                for expression in expressions
                for index, variable in expression._variables.items()
            },
        )
    result = Expression(
        terms=[term for expression in expressions for term in expression.to_terms()],
    )
    result._set_constant_contributions(constant_contributions)
    return result


def dot(coefficients, variables):
    """Return the dot product of coefficients and decision variables."""

    try:
        coefficients = list(coefficients)
        variables = list(variables)
    except TypeError as exc:
        raise InvalidExpressionError("dot arguments must be iterables") from exc
    if len(coefficients) != len(variables):
        raise InvalidExpressionError("dot arguments must have the same length")
    for index, coefficient in enumerate(coefficients):
        if not is_finite_real(coefficient):
            raise InvalidExpressionError(f"dot coefficient {index} must be a finite real number")
    for index, variable in enumerate(variables):
        if not isinstance(variable, Variable):
            raise InvalidExpressionError(f"dot variable {index} must be a decision variable")
    return quicksum(
        coefficient * variable for coefficient, variable in zip(coefficients, variables)
    )
