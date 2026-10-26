"""Exact binary encoding for fixed-point fractional variables."""

from ..constraints.relational import bounded_binary_weights
from ..expressions import quicksum
from ..utils.exceptions import EncodingError
from ..variables import Binary, Fraction, _fixed_point_units, auxiliary_name


def encode_fraction(fraction_var):
    """Encode a fixed-point variable exactly on its declared decimal grid."""

    if not isinstance(fraction_var, Fraction):
        raise EncodingError("encode_fraction expects a Fraction variable")
    declared_domain = (
        fraction_var.lower_bound,
        fraction_var.upper_bound,
        fraction_var.precision_decimals,
    )

    lower_units = _fixed_point_units(
        fraction_var.lower_bound,
        fraction_var.precision_decimals,
        variable_name=fraction_var.name,
        error_type=EncodingError,
    )
    scale = 10 ** int(fraction_var.precision_decimals)
    upper_units = _fixed_point_units(
        fraction_var.upper_bound,
        fraction_var.precision_decimals,
        variable_name=fraction_var.name,
        error_type=EncodingError,
    )
    if lower_units > upper_units:
        raise EncodingError(
            f"Fraction variable '{fraction_var.name}' has an invalid fixed-point domain"
        )
    unit_weights = bounded_binary_weights(upper_units - lower_units)
    stem = auxiliary_name("frac", fraction_var.name, fraction_var.precision_decimals)
    binaries = [Binary(f"{stem}_{index}") for index in range(len(unit_weights))]
    for binary in binaries:
        binary._is_auxiliary = True
    weights = [weight / scale for weight in unit_weights]
    encoded = quicksum(
        [lower_units / scale] + [weight * binary for weight, binary in zip(weights, binaries)]
    )
    encoded.encoding = {
        "source": fraction_var,
        "kind": "fractional",
        "binaries": tuple(binaries),
        "weights": tuple(weights),
        "offset": lower_units / scale,
        "absolute_error": 0.0,
        "step": 1.0 / scale,
        "declared_domain": declared_domain,
    }
    return encoded
