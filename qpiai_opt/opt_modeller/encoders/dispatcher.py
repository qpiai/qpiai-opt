"""Dispatch exact binary encoders for supported non-binary variables."""

import numbers

from ..expressions import Expression, quicksum
from ..utils.exceptions import EncodingError
from .continuous import encode_continuous
from .fraction import encode_fraction
from .integer import encode_integer_binary


def _validate_encoder_options(continuous_bits, continuous_precision, max_bits):
    if continuous_bits is not None and (
        not isinstance(continuous_bits, numbers.Integral)
        or isinstance(continuous_bits, bool)
        or continuous_bits < 1
    ):
        raise EncodingError("continuous_bits must be a positive integer or None")
    if continuous_precision is not None and (
        not isinstance(continuous_precision, numbers.Integral)
        or isinstance(continuous_precision, bool)
        or continuous_precision < 0
    ):
        raise EncodingError("continuous_precision must be a non-negative integer or None")
    if continuous_bits is not None and continuous_precision is not None:
        raise EncodingError("Specify either continuous_bits or continuous_precision, not both")
    if (
        not isinstance(max_bits, numbers.Integral)
        or isinstance(max_bits, bool)
        or not 1 <= max_bits <= 53
    ):
        raise EncodingError("max_bits must be an integer between 1 and 53")


def get_encoder_mapping(
    expression,
    *,
    continuous_bits=None,
    continuous_precision=None,
    max_bits=20,
):
    """Build the source-variable to encoded-expression mapping."""

    return _encode_variables(
        (variable for term in expression.terms for variable in term.variables),
        continuous_bits=continuous_bits,
        continuous_precision=continuous_precision,
        max_bits=max_bits,
    )


def _encode_variables(variables, *, continuous_bits=None, continuous_precision=None, max_bits=20):
    _validate_encoder_options(continuous_bits, continuous_precision, max_bits)
    mapping = {}
    for variable in variables:
        if variable in mapping or variable.var_type == "BINARY":
            continue
        if variable.var_type == "INTEGER":
            mapping[variable] = encode_integer_binary(variable)
        elif variable.var_type == "CONTINUOUS":
            if continuous_bits is None and continuous_precision is None:
                raise EncodingError(
                    f"Continuous variable '{variable.name}' requires an explicit "
                    "continuous_bits or continuous_precision setting"
                )
            mapping[variable] = encode_continuous(
                variable,
                num_bits=continuous_bits,
                precision=continuous_precision,
                max_bits=max_bits,
            )
        elif variable.var_type == "FRACTIONAL":
            mapping[variable] = encode_fraction(variable)
        else:
            raise EncodingError(f"Variable type {variable.var_type} has no exact QUBO encoding")
    return mapping


def dispatch_encoders(
    expression,
    mapping=None,
    *,
    continuous_bits=None,
    continuous_precision=None,
    max_bits=20,
):
    """Substitute exact binary encodings into an expression."""

    if mapping is None:
        mapping = get_encoder_mapping(
            expression,
            continuous_bits=continuous_bits,
            continuous_precision=continuous_precision,
            max_bits=max_bits,
        )
    else:
        _validate_encoder_options(continuous_bits, continuous_precision, max_bits)
    if not mapping:
        return expression

    expanded_terms = []
    for term in expression.terms:
        term_expression = Expression(constant=term.coefficient)
        for variable in term.variables:
            term_expression = term_expression * mapping.get(variable, variable)
        expanded_terms.append(term_expression)
    expanded = quicksum(expanded_terms) + expression.constant
    expanded.encoding_map = {
        source.name: encoded.encoding
        for source, encoded in mapping.items()
        if hasattr(encoded, "encoding")
    }
    return expanded


def expand_expression(expression, **kwargs):
    """Expand an expression with the configured domain encoders."""

    return dispatch_encoders(expression, **kwargs)
