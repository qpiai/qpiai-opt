"""Canonical HUBO dictionaries, validation, inspection, and evaluation."""

import math
from collections import Counter
from collections.abc import Mapping

from ..._numeric import finite_fsum, fsum_by_key, is_finite_real


def _label_key(label):
    return type(label).__name__, repr(label)


def _canonical_term(term):
    return tuple(sorted(set(term), key=_label_key))


def expression_to_polynomial_dict(expression):
    """Convert a symbolic binary expression to a canonical HUBO dictionary."""

    contributions = [((), float(expression.constant))] if expression.constant else []
    names = {}
    for term in expression.terms:
        term_names = []
        for variable in term.variables:
            existing = names.get(variable.name)
            if existing is not None and existing is not variable:
                raise ValueError(f"Expression contains distinct variables named '{variable.name}'")
            names[variable.name] = variable
            term_names.append(variable.name)
        key = _canonical_term(term_names)
        contributions.append((key, float(term.coefficient)))
    try:
        totals = fsum_by_key(contributions)
        return {key: value for key, value in totals.items() if value != 0.0}
    except OverflowError as exc:
        raise ValueError("HUBO coefficient aggregation overflowed; rescale the expression") from exc


def validate_polynomial_dict(polynomial):
    """Validate HUBO term keys, labels, and finite coefficients."""

    if not isinstance(polynomial, dict):
        raise ValueError("Polynomial must be a dictionary")
    for term, coefficient in polynomial.items():
        if not isinstance(term, tuple):
            raise ValueError(f"Polynomial key must be a tuple, got {type(term).__name__}")
        if not is_finite_real(coefficient):
            raise ValueError(f"Coefficient for term {term!r} must be a finite real number")
        for variable in term:
            if isinstance(variable, bool):
                raise ValueError("Boolean values are not valid variable labels")
            try:
                hash(variable)
            except TypeError as exc:
                raise ValueError(f"Variable label {variable!r} is not hashable") from exc
            if isinstance(variable, str) and not variable:
                raise ValueError("Variable labels cannot be empty strings")


def canonicalize_polynomial(polynomial):
    """Combine repeated binary factors and aggregate equivalent HUBO terms."""

    validate_polynomial_dict(polynomial)
    try:
        totals = fsum_by_key(
            (_canonical_term(term), float(coefficient)) for term, coefficient in polynomial.items()
        )
        return {key: value for key, value in totals.items() if value != 0.0}
    except OverflowError as exc:
        raise ValueError("HUBO coefficient aggregation overflowed; rescale the problem") from exc


def calculate_hubo_energy(polynomial, solution):
    """Evaluate a HUBO polynomial at a complete binary assignment."""

    if not isinstance(solution, Mapping):
        raise ValueError("HUBO solution must be a mapping from variable labels to binary values")
    canonical = canonicalize_polynomial(polynomial)
    variables = {variable for term in canonical for variable in term}
    missing = variables - set(solution)
    if missing:
        raise ValueError(
            "Solution is missing variables: "
            + ", ".join(repr(variable) for variable in sorted(missing, key=_label_key))
        )
    invalid = {
        variable: solution[variable]
        for variable in variables
        if solution[variable] not in (0, 1, False, True)
    }
    if invalid:
        raise ValueError(f"HUBO solution values must be binary, got {invalid}")
    contributions = [
        coefficient * math.prod(int(solution[variable]) for variable in term)
        for term, coefficient in canonical.items()
    ]
    try:
        energy = finite_fsum(contributions)
    except OverflowError as exc:
        raise ValueError("HUBO energy evaluation overflowed") from exc
    return energy


def get_polynomial_stats(polynomial):
    """Return variable, term, degree, and constant statistics for a HUBO."""

    canonical = canonicalize_polynomial(polynomial)
    variables = {variable for term in canonical for variable in term}
    counts = Counter(len(term) for term in canonical)
    return {
        "num_variables": len(variables),
        "num_terms": len(canonical),
        "degree": max((len(term) for term in canonical), default=0),
        "constant": canonical.get((), 0.0),
        "term_counts": dict(sorted(counts.items())),
        "variables": sorted(variables, key=_label_key),
    }


def polynomial_to_string(polynomial):
    """Return a readable HUBO polynomial string."""

    canonical = canonicalize_polynomial(polynomial)
    if not canonical:
        return "0"
    parts = []
    for term, coefficient in canonical.items():
        if not term:
            body = f"{abs(coefficient):g}"
        else:
            variables = "*".join(str(variable) for variable in term)
            magnitude = abs(coefficient)
            body = variables if magnitude == 1.0 else f"{magnitude:g}*{variables}"
        parts.append(("-" if coefficient < 0 else "+", body))
    first_sign, first_body = parts[0]
    result = f"-{first_body}" if first_sign == "-" else first_body
    for sign, body in parts[1:]:
        result += f" {sign} {body}"
    return result
