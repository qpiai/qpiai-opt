"""Explicit finite-grid binary encoding for bounded continuous variables."""

import math
import numbers
from decimal import Decimal
from fractions import Fraction

from ..expressions import quicksum
from ..utils.exceptions import EncodingError, NumericalStabilityError
from ..variables import Binary, Continuous, auxiliary_name


def encode_continuous(continuous_var, num_bits=None, precision=None, max_bits=20):
    """Discretize a bounded continuous variable with an explicit resolution."""

    if not isinstance(continuous_var, Continuous):
        raise EncodingError("encode_continuous expects a Continuous variable")
    if num_bits is not None and precision is not None:
        raise EncodingError("Specify either num_bits or precision, not both")
    if num_bits is None and precision is None:
        num_bits = 8
    if (
        not isinstance(max_bits, numbers.Integral)
        or isinstance(max_bits, bool)
        or not 1 <= max_bits <= 53
    ):
        raise EncodingError("max_bits must be an integer between 1 and 53")
    if precision is not None and (
        not isinstance(precision, numbers.Integral) or isinstance(precision, bool) or precision < 0
    ):
        raise EncodingError("precision must be a non-negative integer")
    if num_bits is not None and (
        not isinstance(num_bits, numbers.Integral) or isinstance(num_bits, bool) or num_bits < 1
    ):
        raise EncodingError("num_bits must be a positive integer")
    if num_bits is not None and num_bits > max_bits:
        raise EncodingError(
            f"Encoding '{continuous_var.name}' requires {num_bits} bits, "
            f"exceeding max_bits={max_bits}"
        )

    lower = continuous_var.lower_bound
    upper = continuous_var.upper_bound
    if not math.isfinite(lower) or not math.isfinite(upper):
        raise EncodingError(
            f"Continuous variable '{continuous_var.name}' requires finite bounds for binary encoding"
        )
    if lower > upper:
        raise EncodingError(
            f"Continuous variable '{continuous_var.name}' has lower bound above upper bound"
        )
    if lower == upper:
        encoded = quicksum([lower])
        encoded._encoding_rationals = (Fraction(str(lower)), ())
        encoded.encoding = {
            "source": continuous_var,
            "kind": "continuous",
            "binaries": (),
            "weights": (),
            "offset": lower,
            "absolute_error": 0.0,
        }
        return encoded

    if precision is not None:
        decimal_step = Decimal(1).scaleb(-int(precision))
        step = float(decimal_step)
        rational_step = Fraction(decimal_step)
        if not math.isfinite(step) or step == 0.0:
            raise NumericalStabilityError(f"precision={precision} is not representable in binary64")
        decimal_steps = (Decimal(str(upper)) - Decimal(str(lower))) / decimal_step
        integral_steps = decimal_steps.to_integral_value()
        if decimal_steps != integral_steps:
            raise EncodingError(
                f"Bounds [{lower}, {upper}] are not aligned to precision={precision}"
            )
        maximum_steps = int(integral_steps)
        bits = int(maximum_steps).bit_length()
    else:
        bits = int(num_bits)
        maximum_steps = (1 << bits) - 1
        step = (upper - lower) / maximum_steps
        rational_step = (Fraction(str(upper)) - Fraction(str(lower))) / maximum_steps

    if bits > max_bits:
        raise EncodingError(
            f"Encoding '{continuous_var.name}' requires {bits} bits, exceeding max_bits={max_bits}"
        )
    if not math.isfinite(step) or step == 0.0:
        raise NumericalStabilityError(
            f"Encoding step for '{continuous_var.name}' is not numerically representable"
        )

    from ..constraints.relational import bounded_binary_weights

    integer_weights = bounded_binary_weights(maximum_steps)
    stem = auxiliary_name("cont", continuous_var.name, num_bits, precision, int(max_bits))
    binaries = [Binary(f"{stem}_{index}") for index in range(len(integer_weights))]
    for binary in binaries:
        binary._is_auxiliary = True
    weights = [step * integer_weight for integer_weight in integer_weights]
    encoded = quicksum([lower] + [weight * binary for weight, binary in zip(weights, binaries)])
    # Constraint slack must follow the declared grid, not the unrelated
    # denominators of each rounded binary64 weight.
    encoded._encoding_rationals = (
        Fraction(str(lower)),
        tuple(
            (binary, rational_step * integer_weight)
            for binary, integer_weight in zip(binaries, integer_weights)
        ),
    )
    encoded.encoding = {
        "source": continuous_var,
        "kind": "continuous",
        "binaries": tuple(binaries),
        "weights": tuple(weights),
        "offset": lower,
        "absolute_error": step / 2.0,
        "step": step,
        "declared_bounds": (lower, upper),
    }
    return encoded
