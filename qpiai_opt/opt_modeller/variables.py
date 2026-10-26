"""Decision-variable domains and array constructors."""

import hashlib
import math
import numbers
from decimal import Decimal

import numpy as np

from .._numeric import is_finite_real, is_real_or_infinity
from .utils.exceptions import BoundsError, InvalidExpressionError

_MAX_FIXED_POINT_DECIMALS = 15
_MAX_EXACT_INTEGER = (1 << 53) - 1


def _is_real(value):
    return isinstance(value, numbers.Real) and not isinstance(value, (bool, np.bool_))


def _finite_real(value, *, description):
    if not is_finite_real(value):
        raise BoundsError(f"{description} must be a finite real number, got {value!r}")
    return float(value)


def _fixed_point_units(value, precision_decimals, *, variable_name, error_type=BoundsError):
    if (
        not isinstance(precision_decimals, numbers.Integral)
        or isinstance(precision_decimals, (bool, np.bool_))
        or not 0 <= precision_decimals <= _MAX_FIXED_POINT_DECIMALS
    ):
        raise error_type(
            f"Precision for fractional variable '{variable_name}' must be an integer "
            f"between 0 and {_MAX_FIXED_POINT_DECIMALS}"
        )
    if not is_finite_real(value):
        raise error_type(
            f"Bound {value!r} for fractional variable '{variable_name}' must be finite"
        )
    scaled = Decimal(str(float(value))).scaleb(int(precision_decimals))
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise error_type(
            f"Bound {value!r} for fractional variable '{variable_name}' is not aligned "
            f"to {precision_decimals} decimal places"
        )
    units = int(integral)
    if abs(units) > _MAX_EXACT_INTEGER:
        raise error_type(
            f"Scaled bound for fractional variable '{variable_name}' exceeds the exact "
            "binary64 integer range; reduce its magnitude or decimal precision"
        )
    return units


def auxiliary_name(kind, *parts):
    """Return a portable, deterministic private variable-name stem."""

    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"__qpiai_{kind}_{digest}"


class _VariableType(type):
    def _construction_error(cls, *_args, **_kwargs):
        raise InvalidExpressionError(
            f"{cls.__name__} is a variable constructor, not a decision variable. "
            "Instantiate it first, for example Binary('x') or Continuous('x', 0, 1)."
        )

    __add__ = _construction_error
    __radd__ = _construction_error
    __sub__ = _construction_error
    __rsub__ = _construction_error
    __mul__ = _construction_error
    __rmul__ = _construction_error
    __truediv__ = _construction_error
    __rtruediv__ = _construction_error
    __pow__ = _construction_error
    __rpow__ = _construction_error
    __neg__ = _construction_error
    __pos__ = _construction_error
    __le__ = _construction_error
    __ge__ = _construction_error


class Variable(metaclass=_VariableType):
    """Base symbolic decision variable."""

    __hash__ = object.__hash__

    def __init__(self, name, var_type):
        if not isinstance(name, str) or not name:
            raise InvalidExpressionError(
                "Variable name must be a non-empty string. "
                "Examples: 'x', 'item_0', 'capacity_used'."
            )
        if any(character in name for character in "\x00\r\n"):
            raise InvalidExpressionError("Variable names cannot contain NUL or newline characters")

        self.name = name
        self._var_type = var_type
        self._index = None

    @property
    def var_type(self):
        """Return the canonical variable-domain name."""

        return self._var_type

    @property
    def index(self):
        """Return the expression identity index, if assigned."""

        return self._index

    def _to_expression(self):
        from .expressions import Expression, Term

        term = Term(1.0, [self])
        if self.index is None:
            return Expression(terms=[term])
        return Expression._from_vectorized(
            np.array([1.0], dtype=np.float64),
            np.array([[self.index, -1]], dtype=np.int64),
            terms=[term],
            variables={self.index: self},
        )

    def __pow__(self, power):
        from .expressions import _MAX_TERM_FACTORS, Expression, Term

        if (
            not isinstance(power, numbers.Integral)
            or isinstance(power, (bool, np.bool_))
            or power < 0
        ):
            raise InvalidExpressionError(
                f"Invalid power {power!r} for variable '{self.name}'. Use a non-negative integer."
            )
        if power == 0:
            return Expression(constant=1.0)
        if power == 1:
            return self._to_expression()
        power = int(power)
        if self.var_type == "BINARY":
            return self._to_expression()
        if self.var_type == "SPIN":
            return self._to_expression() if power % 2 else Expression(constant=1.0)
        if power > _MAX_TERM_FACTORS:
            raise InvalidExpressionError(
                f"Polynomial power {power} for variable '{self.name}' would retain more than "
                f"{_MAX_TERM_FACTORS:,} factors"
            )
        return Expression(terms=[Term(1.0, [self] * power)])

    def __rpow__(self, base):
        raise InvalidExpressionError(
            f"Variable exponents are outside the modeller's expression set: "
            f"{base!r} ** {self.name}. Use x ** 2 for a polynomial term."
        )

    def __repr__(self):
        return f"{self.name}[{self.var_type}]"

    def __add__(self, other):
        return self._to_expression() + other

    def __radd__(self, other):
        return self._to_expression() + other

    def __sub__(self, other):
        return self._to_expression() - other

    def __rsub__(self, other):
        return other - self._to_expression()

    def __mul__(self, other):
        from .expressions import Expression, Term

        if _is_real(other):
            term = Term(other, [self])
            if self.index is None:
                return Expression(terms=[term])
            return Expression._from_vectorized(
                np.array([float(other)], dtype=np.float64),
                np.array([[self.index, -1]], dtype=np.int64),
                terms=[term],
                variables={self.index: self},
            )
        if isinstance(other, Variable):
            term = Term(1.0, [self, other])
            if self.index is not None and other.index is not None:
                return Expression._from_vectorized(
                    np.array([1.0], dtype=np.float64),
                    np.array([[self.index, other.index]], dtype=np.int64),
                    terms=[term],
                    variables={self.index: self, other.index: other},
                )
            return Expression(terms=[term])
        return NotImplemented

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        if not _is_real(other):
            raise InvalidExpressionError(
                "Decision variables can only be divided by a finite real scalar"
            )
        try:
            divisor = float(other)
        except (OverflowError, TypeError, ValueError) as exc:
            raise InvalidExpressionError("Variable divisor must be a finite real scalar") from exc
        if divisor == 0.0:
            raise ZeroDivisionError("division by zero")
        if not math.isfinite(divisor):
            raise InvalidExpressionError("Variable divisor must be finite")
        return self._to_expression() / divisor

    def __rtruediv__(self, other):
        raise InvalidExpressionError(
            "Division by a decision variable is not supported; use a finite scalar divisor"
        )

    def __neg__(self):
        return -1.0 * self

    def __pos__(self):
        return self._to_expression()

    def __le__(self, other):
        from .constraints.relational import LessEqual

        return LessEqual(self, other)

    def __ge__(self, other):
        from .constraints.relational import GreaterEqual

        return GreaterEqual(self, other)

    def __eq__(self, other):
        from .constraints.relational import Equal

        return Equal(self, other)


class Binary(Variable):
    """Binary decision variable with domain ``{0, 1}``."""

    def __init__(self, name):
        super().__init__(name, var_type="BINARY")
        self._index = id(self)


class Spin(Variable):
    """An Ising spin decision variable with domain ``{-1, +1}``."""

    def __init__(self, name):
        super().__init__(name, var_type="SPIN")


class Integer(Variable):
    """Integer decision variable over declared bounds."""

    def __init__(self, name, lower_bound=0, upper_bound=None):
        upper_bound = math.inf if upper_bound is None else upper_bound

        def normalize_bound(value, description):
            if isinstance(value, numbers.Integral) and not isinstance(value, (bool, np.bool_)):
                integer = int(value)
                if abs(integer) <= _MAX_EXACT_INTEGER:
                    return integer
            elif _is_real(value):
                try:
                    numeric = float(value)
                except (OverflowError, ValueError):
                    numeric = math.nan
                if math.isinf(numeric):
                    return numeric
                if numeric.is_integer() and abs(numeric) <= _MAX_EXACT_INTEGER:
                    return int(numeric)
            raise BoundsError(
                f"{description} for integer variable '{name}' must be an exactly "
                "representable integer or infinity"
            )

        lower = normalize_bound(lower_bound, "Lower bound")
        upper = normalize_bound(upper_bound, "Upper bound")
        if lower > upper or lower == math.inf or upper == -math.inf:
            raise BoundsError(
                f"Invalid integer bounds [{lower_bound}, {upper_bound}] for variable '{name}'"
            )
        super().__init__(name, var_type="INTEGER")
        self.lower_bound = lower
        self.upper_bound = upper


class Continuous(Variable):
    """Continuous decision variable over a real interval."""

    def __init__(self, name, lower_bound=0.0, upper_bound=math.inf):
        if not is_real_or_infinity(lower_bound) or not is_real_or_infinity(upper_bound):
            raise BoundsError(f"Bounds for continuous variable '{name}' must be real numbers")
        lower = float(lower_bound)
        upper = float(upper_bound)
        if lower > upper or lower == math.inf or upper == -math.inf:
            raise BoundsError(
                f"Invalid continuous bounds [{lower_bound}, {upper_bound}] for variable '{name}'"
            )
        super().__init__(name, var_type="CONTINUOUS")
        self.lower_bound = lower
        self.upper_bound = upper


class SemiContinuous(Variable):
    """Decision variable equal to zero or within a positive interval."""

    def __init__(self, name, lower_bound, upper_bound):
        lower = _finite_real(lower_bound, description=f"Lower bound for '{name}'")
        upper = _finite_real(upper_bound, description=f"Upper bound for '{name}'")
        if lower <= 0:
            raise BoundsError(
                f"Semi-continuous variable '{name}' must have a strictly positive lower bound"
            )
        if lower > upper:
            raise BoundsError(
                f"Lower bound ({lower_bound}) cannot exceed upper bound ({upper_bound}) "
                f"for variable '{name}'"
            )
        super().__init__(name, var_type="SEMI_CONTINUOUS")
        self.lower_bound = lower
        self.upper_bound = upper


class SemiInteger(Variable):
    """Integer decision variable equal to zero or within a positive interval."""

    def __init__(self, name, lower_bound, upper_bound):
        def normalize(value):
            if isinstance(value, numbers.Integral) and not isinstance(value, (bool, np.bool_)):
                integer = int(value)
                if abs(integer) <= _MAX_EXACT_INTEGER:
                    return integer
            elif _is_real(value):
                try:
                    numeric = float(value)
                except (OverflowError, ValueError):
                    numeric = math.nan
                if (
                    math.isfinite(numeric)
                    and numeric.is_integer()
                    and abs(numeric) <= _MAX_EXACT_INTEGER
                ):
                    return int(numeric)
            raise BoundsError(
                f"Bounds for semi-integer variable '{name}' must be exactly "
                "representable finite integers"
            )

        lower = normalize(lower_bound)
        upper = normalize(upper_bound)
        if lower <= 0 or lower > upper:
            raise BoundsError(
                f"Semi-integer variable '{name}' requires 0 < lower_bound <= upper_bound"
            )
        super().__init__(name, var_type="SEMI_INTEGER")
        self.lower_bound = lower
        self.upper_bound = upper


class Fraction(Variable):
    """Fixed-point decimal decision variable with explicit precision."""

    def __init__(self, name, lower_bound, upper_bound, precision_decimals=2):
        lower = _finite_real(lower_bound, description=f"Lower bound for '{name}'")
        upper = _finite_real(upper_bound, description=f"Upper bound for '{name}'")
        if lower > upper:
            raise BoundsError(
                f"Lower bound ({lower_bound}) cannot exceed upper bound ({upper_bound}) "
                f"for variable '{name}'"
            )
        _fixed_point_units(
            lower,
            precision_decimals,
            variable_name=name,
        )
        _fixed_point_units(
            upper,
            precision_decimals,
            variable_name=name,
        )
        super().__init__(name, var_type="FRACTIONAL")
        self.lower_bound = lower
        self.upper_bound = upper
        self.precision_decimals = int(precision_decimals)


class Mixed(Variable):
    """Decision variable over the union of discrete values and a real interval."""

    def __init__(self, name, valid_discrete_values, continuous_range):
        try:
            values = list(valid_discrete_values)
        except TypeError as exc:
            raise BoundsError(
                f"Mixed variable '{name}' requires finite numeric discrete values"
            ) from exc
        if not values or any(not is_finite_real(value) for value in values):
            raise BoundsError(f"Mixed variable '{name}' requires finite numeric discrete values")
        if len({float(value) for value in values}) != len(values):
            raise BoundsError(f"Mixed variable '{name}' contains duplicate discrete values")
        if not isinstance(continuous_range, (tuple, list)) or len(continuous_range) != 2:
            raise BoundsError(
                f"Mixed variable '{name}' continuous_range must be a (lower, upper) pair"
            )
        lower = _finite_real(
            continuous_range[0], description=f"Continuous lower bound for '{name}'"
        )
        upper = _finite_real(
            continuous_range[1], description=f"Continuous upper bound for '{name}'"
        )
        if lower > upper:
            raise BoundsError(f"Mixed variable '{name}' has an invalid continuous range")
        super().__init__(name, var_type="MIXED")
        self.valid_discrete_values = tuple(float(value) for value in values)
        self.continuous_range = (lower, upper)


def create_binary_array(prefix, size):
    """Return ``size`` binary variables named from ``prefix``."""

    if not isinstance(prefix, str) or not prefix:
        raise InvalidExpressionError(
            "Binary array prefix must be a non-empty string. Example: create_binary_array('x', 5)."
        )
    if not isinstance(size, numbers.Integral) or isinstance(size, (bool, np.bool_)) or size < 0:
        raise InvalidExpressionError(
            f"Binary array size must be a non-negative integer, got {size!r}"
        )
    return [Binary(f"{prefix}_{index}") for index in range(int(size))]


def create_spin_array(prefix, size):
    """Return ``size`` spin variables named from ``prefix``."""

    if not isinstance(prefix, str) or not prefix:
        raise InvalidExpressionError(
            "Spin array prefix must be a non-empty string. Example: create_spin_array('s', 5)."
        )
    if not isinstance(size, numbers.Integral) or isinstance(size, (bool, np.bool_)) or size < 0:
        raise InvalidExpressionError(
            f"Spin array size must be a non-negative integer, got {size!r}"
        )
    return [Spin(f"{prefix}_{index}") for index in range(int(size))]
