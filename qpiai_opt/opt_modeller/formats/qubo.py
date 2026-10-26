"""Sparse QUBO representations, validation, conversion, and evaluation."""

import numbers

import numpy as np
from scipy.sparse import coo_matrix

from ..._numeric import finite_fsum, is_finite_real
from ..utils.exceptions import InvalidQUBOError, ModelCompilationError

QUBO_MATRIX_FORMATS = frozenset({"symmetric", "upper"})


def normalize_qubo_matrix_format(matrix_format):
    """Validate and normalize a public QUBO matrix representation name."""

    if not isinstance(matrix_format, str):
        raise InvalidQUBOError("matrix_format must be 'symmetric' or 'upper'")
    normalized = matrix_format.strip().lower().replace("-", "_")
    aliases = {
        "fully_symmetric": "symmetric",
        "upper_triangular": "upper",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in QUBO_MATRIX_FORMATS:
        raise InvalidQUBOError("matrix_format must be 'symmetric' or 'upper'")
    return normalized


def _validated_triplets(rows, cols, coefficients, num_vars):
    if not isinstance(num_vars, numbers.Integral) or isinstance(num_vars, bool) or num_vars < 0:
        raise InvalidQUBOError("num_vars must be a non-negative integer")

    def index_array(values, description):
        raw = np.asarray(values)
        if raw.ndim != 1:
            raise InvalidQUBOError(f"QUBO {description} must be one-dimensional")
        if raw.size == 0:
            return np.empty(0, dtype=np.int64)
        if raw.dtype.kind not in {"i", "u"} or raw.dtype.kind == "b":
            if raw.dtype.kind != "O" or any(
                not isinstance(value, numbers.Integral) or isinstance(value, bool) for value in raw
            ):
                raise InvalidQUBOError(f"QUBO {description} must contain integers")
        try:
            normalized = raw.astype(np.int64, casting="unsafe", copy=False)
        except (OverflowError, TypeError, ValueError) as exc:
            raise InvalidQUBOError(f"QUBO {description} exceed int64 range") from exc
        if raw.dtype.kind == "u" and np.any(raw > np.iinfo(np.int64).max):
            raise InvalidQUBOError(f"QUBO {description} exceed int64 range")
        return normalized

    def coefficient_array(values):
        raw = np.asarray(values)
        if raw.ndim != 1:
            raise InvalidQUBOError("QUBO coefficients must be one-dimensional")
        if raw.dtype.kind not in {"i", "u", "f"} or raw.dtype.kind == "b":
            if raw.dtype.kind != "O" or any(
                not isinstance(value, numbers.Real) or isinstance(value, bool) for value in raw
            ):
                raise InvalidQUBOError("QUBO coefficients must be real numbers")
        try:
            return raw.astype(np.float64, casting="unsafe", copy=False)
        except (OverflowError, TypeError, ValueError) as exc:
            raise InvalidQUBOError("QUBO coefficients are not representable") from exc

    rows = index_array(rows, "row indices")
    cols = index_array(cols, "column indices")
    coefficients = coefficient_array(coefficients)
    if rows.ndim != 1 or cols.ndim != 1 or coefficients.ndim != 1:
        raise InvalidQUBOError("QUBO triplet arrays must be one-dimensional")
    if not (len(rows) == len(cols) == len(coefficients)):
        raise InvalidQUBOError("QUBO triplet arrays must have equal lengths")
    if coefficients.size and not np.all(np.isfinite(coefficients)):
        raise InvalidQUBOError("QUBO coefficients must be finite")
    if rows.size and (
        np.any(rows < 0) or np.any(cols < 0) or np.any(rows >= num_vars) or np.any(cols >= num_vars)
    ):
        raise InvalidQUBOError("QUBO indices are outside [0, num_vars)")
    return rows, cols, coefficients, int(num_vars)


def _canonical_upper(rows, cols, coefficients, num_vars):
    rows, cols, coefficients, num_vars = _validated_triplets(rows, cols, coefficients, num_vars)
    lower = np.minimum(rows, cols)
    upper = np.maximum(rows, cols)
    if len(coefficients):
        order = np.lexsort((upper, lower))
        lower = lower[order]
        upper = upper[order]
        coefficients = coefficients[order]
        starts = np.r_[
            0,
            np.flatnonzero((lower[1:] != lower[:-1]) | (upper[1:] != upper[:-1])) + 1,
        ]
        stops = np.r_[starts[1:], len(coefficients)]
        totals = coefficients[starts].copy()
        try:
            for index in np.flatnonzero(stops - starts > 1):
                totals[index] = finite_fsum(coefficients[starts[index] : stops[index]])
        except OverflowError as exc:
            raise InvalidQUBOError("QUBO coefficient aggregation overflowed") from exc
        nonzero = totals != 0.0
        lower = lower[starts][nonzero]
        upper = upper[starts][nonzero]
        coefficients = totals[nonzero]
    return coo_matrix(
        (coefficients, (lower, upper)),
        shape=(num_vars, num_vars),
        dtype=np.float64,
    )


def format_qubo_triplets(
    rows,
    cols,
    coefficients,
    num_vars,
    *,
    matrix_format="symmetric",
):
    """Return canonical triplets in symmetric or direct upper-triangular form.

    Upper-triangular off-diagonal values are direct polynomial coefficients.
    Symmetric output splits each such value equally across ``(i, j)`` and
    ``(j, i)`` so both representations evaluate to the same energy.
    """

    upper = _canonical_upper(rows, cols, coefficients, num_vars)
    return _format_canonical_upper_triplets(
        upper.row,
        upper.col,
        upper.data,
        matrix_format=matrix_format,
    )


def _format_canonical_upper_triplets(
    rows,
    cols,
    coefficients,
    *,
    matrix_format,
):
    """Format already validated, unique upper-triangular triplets."""

    normalized = normalize_qubo_matrix_format(matrix_format)
    if normalized == "upper" or len(coefficients) == 0:
        return rows.copy(), cols.copy(), coefficients.copy()

    off_diagonal = rows != cols
    symmetric_rows = np.concatenate((rows, cols[off_diagonal]))
    symmetric_cols = np.concatenate((cols, rows[off_diagonal]))
    first_values = coefficients.copy()
    first_values[off_diagonal] /= 2.0
    if np.any((coefficients[off_diagonal] != 0.0) & (first_values[off_diagonal] == 0.0)):
        raise InvalidQUBOError(
            "QUBO symmetrization underflowed an off-diagonal coefficient; rescale the model"
        )
    symmetric_values = np.concatenate((first_values, coefficients[off_diagonal] / 2.0))
    order = np.lexsort((symmetric_cols, symmetric_rows))
    return (
        symmetric_rows[order].astype(np.int64, copy=False),
        symmetric_cols[order].astype(np.int64, copy=False),
        symmetric_values[order].astype(np.float64, copy=False),
    )


def to_scipy_sparse(
    rows,
    cols,
    coeffs,
    num_vars,
    *,
    matrix_format="symmetric",
    sparse_format="csr",
):
    """Return a canonical QUBO matrix in CSR or COO storage."""

    if not isinstance(sparse_format, str) or sparse_format not in ("csr", "coo"):
        raise InvalidQUBOError("sparse_format must be 'csr' or 'coo'")
    rows, cols, coefficients = format_qubo_triplets(
        rows,
        cols,
        coeffs,
        num_vars,
        matrix_format=matrix_format,
    )
    matrix = coo_matrix(
        (coefficients, (rows, cols)),
        shape=(int(num_vars), int(num_vars)),
        dtype=np.float64,
    )
    return matrix.tocsr() if sparse_format == "csr" else matrix


def to_qubo_dict(
    rows,
    cols,
    coeffs,
    num_vars,
    *,
    matrix_format="symmetric",
):
    """Return a canonical QUBO dictionary in the requested matrix format."""

    formatted_rows, formatted_cols, formatted_coefficients = format_qubo_triplets(
        rows,
        cols,
        coeffs,
        num_vars,
        matrix_format=matrix_format,
    )
    return {
        (int(row), int(col)): float(coefficient)
        for row, col, coefficient in zip(
            formatted_rows,
            formatted_cols,
            formatted_coefficients,
        )
    }


def calculate_qubo_energy(qubo, sample, constant=0.0):
    """Evaluate a QUBO dictionary exactly as represented.

    This works for direct upper-triangular dictionaries and for fully symmetric
    dictionaries whose mirrored off-diagonal values are half coefficients.
    """

    if not isinstance(qubo, dict):
        raise InvalidQUBOError("QUBO must be a dictionary")
    values = sample
    if not is_finite_real(constant):
        raise InvalidQUBOError("QUBO constant must be finite")
    contributions = [float(constant)]
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
            raise InvalidQUBOError(f"Invalid QUBO coefficient for key {key!r}")
        row, col = key
        try:
            left = values[row]
            right = values[col]
        except (KeyError, IndexError, TypeError) as exc:
            raise InvalidQUBOError(f"Sample is missing QUBO variable {key!r}") from exc
        if left not in (0, 1, False, True) or right not in (0, 1, False, True):
            raise InvalidQUBOError("QUBO sample values must be binary")
        contributions.append(float(coefficient) * int(left) * int(right))
    try:
        energy = finite_fsum(contributions)
    except OverflowError as exc:
        raise InvalidQUBOError("QUBO energy evaluation overflowed") from exc
    return energy


def calculate_true_objective(solver_energy, constant_offset, sense):
    """Restore a source objective from normalized energy, offset, and sense."""

    if not is_finite_real(solver_energy) or not is_finite_real(constant_offset):
        raise ModelCompilationError("Solver energy and constant offset must be finite")
    if not isinstance(sense, str):
        raise ModelCompilationError("Optimization sense must be MIN or MAX")
    normalized = sense.upper()
    if normalized not in {"MIN", "MAX"}:
        raise ModelCompilationError(f"Unknown optimization sense {sense!r}; use MIN or MAX")
    try:
        normalized_value = finite_fsum([float(solver_energy), float(constant_offset)])
    except OverflowError as exc:
        raise ModelCompilationError(
            "Solver energy and constant offset overflowed during objective restoration"
        ) from exc
    return -normalized_value if normalized == "MAX" else normalized_value
