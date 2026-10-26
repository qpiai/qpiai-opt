# Representation utility API

Import representation helpers from `qpiai_opt.opt_modeller.formats`. These functions validate independently supplied QUBO/HUBO data, convert sparse QUBO storage, evaluate energies, and build LP text plus matrices from the documented dictionary form.

The separate `qpiai_opt.opt_modeller.compiler` namespace exports the low-level `lower_expression()` operation documented below.

```python
from qpiai_opt.opt_modeller.formats import calculate_qubo_energy, to_qubo_dict
```

QUBO indices are zero-based integers in `[0, num_vars)`. Coefficients and constants must be finite real numbers. `matrix_format="upper"` stores each off-diagonal polynomial coefficient once; `matrix_format="symmetric"` stores half on each mirrored entry so $x^TQx$ is unchanged. Format names are stripped, lowercased, and normalize hyphens to underscores; accepted aliases are `upper_triangular` and `fully_symmetric`. Prefer the two canonical names in stored configuration.

All numeric inputs are normalized to IEEE-754 binary64. Decimal-to-binary rounding is therefore expected; these helpers do not provide arbitrary-precision decimal arithmetic. Keep coefficients and constants within the finite binary64 range and scale values that approach it. Integers above `2**53` may round and are not suitable where exact integer meaning is needed. `decimal.Decimal` is not an accepted numeric input type. Use a supported Python or NumPy real value instead.

## Function: `lower_expression`

Import from `qpiai_opt.opt_modeller.compiler`.

```text
lower_expression(expression)
```

Accept an `Expression` or `Variable` containing binary factors only. Return `(rows, columns, coefficients, constant)` as three equal-length one-dimensional NumPy arrays with dtypes `int64`, `int64`, and `float64`, plus a `float` constant. Triplets are upper-triangular direct polynomial coefficients, sorted by index pair, with duplicate contributions summed and zero totals removed.

Columns are local to this expression and follow first encounter in its nonzero terms. They are not `Variable.index` or a model's compiled QUBO indices. Binary powers reduce by `x**n = x`; distinct source objects with the same name are rejected. This operation does not register variables, encode nonbinary domains, add constraint penalties, or normalize a model's MIN/MAX objective direction. Use `Model.to_qubo_triplets()` when model compilation and decoding metadata are required.

Wrong input types raise `TypeError`; nonbinary factors, duplicate source names, or coefficient-aggregation overflow raise `InvalidQUBOError`; a surviving term with more than two binary factors raises `HigherOrderTermError`.

## Function: `format_qubo_triplets`

```text
format_qubo_triplets(rows, cols, coefficients, num_vars, *, matrix_format='symmetric')
```

Accept one-dimensional array-like `rows`, `cols`, and `coefficients` of equal length, plus nonnegative integral `num_vars` (excluding Boolean values). Index arrays must contain integral values representable as signed `int64`, within `[0, num_vars)`; floating-point index arrays are rejected even when all values are whole numbers. Coefficient arrays must be real and finite after binary64 conversion. Empty arrays and `num_vars=0` are accepted when no index is present.

Return `(rows, cols, coefficients)` as new one-dimensional NumPy arrays with equal length and lexicographically sorted coordinates. Coefficients have dtype `float64`. Symmetric output has `int64` indices; upper output retains the index width selected by SciPy for the canonical matrix, commonly `int32` for smaller shapes and `int64` when required.

Every input triplet contributes directly to its monomial: duplicate coordinates and mirrored pairs are added, then zero totals are removed. `matrix_format` selects the output representation, not an instruction to halve the input twice. Thus two mirrored values `q/2` become one upper coefficient `q`, or two symmetric values `q/2`. Invalid shapes, values, sizes, formats, aggregation overflow, or symmetrization that erases a nonzero coefficient raise `InvalidQUBOError`.

## Function: `to_scipy_sparse`

```text
to_scipy_sparse(rows, cols, coeffs, num_vars, *, matrix_format='symmetric', sparse_format='csr')
```

Return a `float64` SciPy `csr_matrix` for `sparse_format="csr"` or `coo_matrix` for `"coo"`, with shape `(num_vars, num_vars)`. These storage-format names are exact lowercase strings. `matrix_format` independently selects symmetric or upper coefficients. Inputs, aggregation, and errors follow `format_qubo_triplets()`; an unsupported sparse storage format raises `InvalidQUBOError`.

## Function: `to_qubo_dict`

```text
to_qubo_dict(rows, cols, coeffs, num_vars, *, matrix_format='symmetric')
```

Return `dict[tuple[int, int], float]` using Python integer coordinates and float coefficients. Inputs, canonical aggregation, output matrix conventions, and errors follow `format_qubo_triplets()`. The dictionary does not carry a constant offset or preserve an isolated-variable count; retain those separately.

```python
from qpiai_opt.opt_modeller.formats import to_qubo_dict, to_scipy_sparse

rows = [0, 0, 1]
columns = [0, 1, 1]
coefficients = [-1.0, 2.0, -1.0]

qubo = to_qubo_dict(rows, columns, coefficients, 2, matrix_format="upper")
matrix = to_scipy_sparse(rows, columns, coefficients, 2)

qubo  # {(0, 0): -1.0, (0, 1): 2.0, (1, 1): -1.0}
matrix.toarray()  # [[-1.0, 1.0], [1.0, -1.0]]
```

## Function: `calculate_qubo_energy`

```text
calculate_qubo_energy(qubo, sample, constant=0.0)
```

Require a QUBO `dict` with two-index tuple keys, nonnegative non-Boolean integral indices, and finite non-Boolean real coefficients. `sample` supplies every referenced index through a sequence or integer-keyed mapping; values equal to `0` or `1`, including Boolean bits, are accepted. Extra sample entries are ignored. Referenced bits are checked even for zero-valued coefficients.

Return `float` for `constant + sum(q_ij * x_i * x_j)` exactly as the dictionary is represented. Both upper and symmetric half-coefficient dictionaries work without another format argument. `constant` defaults to zero and must be finite and non-Boolean. Invalid keys, coefficients, bits, missing indices, or unrepresentable aggregation raise `InvalidQUBOError`.

## Function: `calculate_true_objective`

```text
calculate_true_objective(solver_energy, constant_offset, sense)
```

Return `float` restoring source coordinates after minimization normalization. For `MIN`, return `solver_energy + constant_offset`; for `MAX`, negate that sum. Both numeric inputs must be finite non-Boolean real values. Sense is case-insensitive `MIN` or `MAX`, without whitespace stripping. Invalid inputs or unrepresentable aggregation raise `ModelCompilationError`.

Supply the normalized offset only when `solver_energy` excludes it. If the energy already includes the offset, pass `constant_offset=0.0` to avoid counting it twice. This helper does not decode source variables or evaluate the original objective after a penalty transformation.

## Function: `validate_polynomial_dict`

```text
validate_polynomial_dict(polynomial)
```

Require an actual `dict` and return `None` after validation. Keys must be tuples of hashable labels; Python Boolean labels and empty string labels are rejected. The empty tuple represents a constant. Coefficients must be finite non-Boolean real values representable as binary64. An empty dictionary is valid. Repeated factors and zero coefficients are accepted here; validation alone does not canonicalize or mutate the input. Invalid input raises `ValueError`.

## Function: `canonicalize_polynomial`

```text
canonicalize_polynomial(polynomial)
```

Return a new `dict[tuple[label, ...], float]` after validation. Collapse repeated factors by the binary identity, order factors by `(type(label).__name__, repr(label))`, aggregate equivalent monomials, and drop zero totals. Monomials retain their first-occurrence insertion order rather than being globally sorted. Invalid data or aggregation overflow raises `ValueError`; the input dictionary is unchanged.

## Function: `calculate_hubo_energy`

```text
calculate_hubo_energy(polynomial, solution)
```

Accept a polynomial dictionary and a `Mapping` from its canonical variable labels to binary values. Values equal to `0` or `1`, including Booleans, are accepted. Only labels surviving canonical zero removal are required; extra assignment entries are ignored. Return the finite `float` energy, including the empty-tuple constant. An empty polynomial evaluates to `0.0`. Invalid polynomial data, non-mapping samples, missing labels, nonbinary values, or aggregation overflow raise `ValueError`.

## Function: `get_polynomial_stats`

```text
get_polynomial_stats(polynomial)
```

Return a `dict` with `num_variables: int`, `num_terms: int`, `degree: int`, `constant: float`, `term_counts: dict[int, int]`, and `variables: list[label]` for the canonical polynomial. A retained constant counts as one degree-zero term. Variable order follows `(type(label).__name__, repr(label))`; labels occurring only in removed zero terms are absent. An empty polynomial reports zero counts, degree zero, constant `0.0`, and empty collections. Validation and overflow errors follow `canonicalize_polynomial()`.

## Function: `polynomial_to_string`

```text
polynomial_to_string(polynomial)
```

Return `str` for the canonical polynomial in first-occurrence monomial order; an empty polynomial becomes `"0"`. Factor order is canonical, but changing input monomial insertion order can change the displayed order. Coefficients use general numeric display formatting, so this human-readable value is not a lossless serialization format. Validation and overflow errors follow `canonicalize_polynomial()`.

```python
from qpiai_opt.opt_modeller.formats import (
    calculate_hubo_energy,
    get_polynomial_stats,
    polynomial_to_string,
)

polynomial = {("x", "x", "y"): -2.0, ("x",): 1.0, (): 0.5}
sample = {"x": 1, "y": 0}

polynomial_to_string(polynomial)  # "-2*x*y + x + 0.5"
get_polynomial_stats(polynomial)
calculate_hubo_energy(polynomial, sample)  # 1.5
```

The statistics report two variables, three terms, degree two, constant `0.5`, term counts `{0: 1, 1: 1, 2: 1}`, and variables `["x", "y"]`.

## Function: `generate_lp`

```text
generate_lp(model_dict)
```

Return `(lp_text: str, matrix_data: dict)` after validating the [LP dictionary interchange form](milp-modeller.md#strict-dictionary-interchange). The matrix dictionary uses the complete [linear matrix contract](milp-modeller.md#matrix-representation), with CSR coefficients and SOS reformulation enabled. The string is newline-terminated LP text; no file is written and no solver runs.

The dictionary must declare at least one variable through nonzero terms, bounds, type lists, or SOS membership; a variable-free constant dictionary is rejected even though an algebraic `Model` can represent a constant-only problem. The function rejects unknown fields, invalid senses, overlapping type sections, inconsistent bounds, malformed constraints, and invalid SOS data. Dictionary-schema errors raise `ValueError`; model construction and export can raise their typed modeller exceptions, including portable-name validation failures.
