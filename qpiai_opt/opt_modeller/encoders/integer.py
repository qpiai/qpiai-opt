"""Exact bounded binary encoding for integer variables."""

from ..._numeric import is_finite_real
from ..constraints.relational import bounded_binary_weights
from ..expressions import quicksum
from ..utils.exceptions import EncodingError
from ..variables import _MAX_EXACT_INTEGER, Binary, Integer, auxiliary_name


def encode_integer_binary(integer_var):
    """Encode every integer in the declared closed interval, and no value above it."""

    if not isinstance(integer_var, Integer):
        raise EncodingError("encode_integer_binary expects an Integer variable")
    declared_bounds = (
        integer_var.lower_bound,
        integer_var.upper_bound,
    )

    lower = integer_var.lower_bound
    upper = integer_var.upper_bound
    if not is_finite_real(lower) or not is_finite_real(upper):
        raise EncodingError(
            f"Integer variable '{integer_var.name}' requires finite bounds for QUBO encoding"
        )
    if not float(lower).is_integer() or not float(upper).is_integer() or lower > upper:
        raise EncodingError(f"Integer variable '{integer_var.name}' has invalid integer bounds")
    if abs(int(lower)) > _MAX_EXACT_INTEGER or abs(int(upper)) > _MAX_EXACT_INTEGER:
        raise EncodingError(
            f"Integer variable '{integer_var.name}' exceeds exact binary64 integer resolution"
        )
    lower = int(lower)
    maximum_offset = int(upper) - lower
    weights = bounded_binary_weights(maximum_offset)
    stem = auxiliary_name("int", integer_var.name)
    binaries = [Binary(f"{stem}_{index}") for index in range(len(weights))]
    for binary in binaries:
        binary._is_auxiliary = True
    encoded = quicksum(
        [float(lower)] + [float(weight) * binary for weight, binary in zip(weights, binaries)]
    )
    encoded.encoding = {
        "source": integer_var,
        "kind": "integer",
        "binaries": tuple(binaries),
        "weights": tuple(float(weight) for weight in weights),
        "offset": float(lower),
        "absolute_error": 0.0,
        "declared_bounds": declared_bounds,
    }
    return encoded
