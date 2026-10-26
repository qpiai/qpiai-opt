# HUBO modeller

A HUBO is a finite-degree polynomial over binary variables. Unlike QUBO, terms may contain three or more distinct variables.

## Mathematical form

```latex
$$
H(x)=c+\sum_{S}a_S\prod_{i\in S}x_i,\qquad x_i\in\{0,1\}.
$$
```

Binary idempotence applies: `x**k == x` for every positive integer `k`.

## Build with expressions

```python
from qpiai_opt.opt_modeller import Binary, Model

x = Binary("x")
y = Binary("y")
z = Binary("z")

model = Model("cubic")
model.minimize(2 * x * y * z - 3 * x + x * y + 7)

polynomial = model.to_hubo_dict()
```

`to_hubo_dict()` returns the polynomial mapping:

```python
{
    (): 7.0,
    ("x",): -3.0,
    ("x", "y"): 1.0,
    ("x", "y", "z"): 2.0,
}
```

Tuple keys are canonical variable-label sets:

- `()` is the constant;
- repeated factors are removed;
- labels in each term are sorted deterministically;
- equivalent coefficients are added;
- zero terms are omitted.

The output is always a minimization energy. If the source model uses `maximize()`, compilation negates the entire source objective. Retain `model.sense` when a later consumer must restore the source value.

After compilation, `model.hubo_variable_names` contains the compiled binary labels, including registered isolated variables and encoding bits.

Decode a solver sample after the same model instance has been compiled:

```python
decoded = model.decode_hubo_sample(
    {"x": 1, "y": 0, "z": 1}
)
source_objective = model.evaluate_objective(decoded)
```

`decode_hubo_sample()` also accepts a sequence in `hubo_variable_names` order. It rejects a wrong-length sequence, missing compiled names, conflicting name/index values, or non-binary values. The result contains original declared variables and reverses integer, fractional, and discretized-continuous encodings; compiler auxiliaries are omitted.

## Supported model structures

Binary objectives may contain terms of any finite degree. The modeller canonicalizes repeated binary factors before reporting the degree, so `x * x * y` is the degree-two term `x * y`.

Variables may be:

- `Binary`, without encoding;
- bounded `Integer`, encoded exactly;
- `Fraction`, encoded exactly on its fixed-point grid;
- bounded `Continuous`, with an explicit finite discretization.

```python
from qpiai_opt.opt_modeller import Continuous

flow = Continuous("flow", 0, 1)
continuous_model = Model("cubic_flow")
continuous_model.minimize((flow - 0.5) ** 3)
continuous_polynomial = continuous_model.to_hubo_dict(continuous_bits=10)
# or
continuous_polynomial = continuous_model.to_hubo_dict(continuous_precision=3)
```

As in QUBO compilation, `max_bits` bounds the number of bits per continuous encoding. Source polynomial terms do not have a fixed modelling-degree limit. The selected conversion or solver validates its own capabilities after any variable encoding. Semi-continuous, semi-integer, mixed, and spin domains do not have an implicit HUBO encoding and are rejected.

SOS and indicator constraints require LP/MILP export.

## Relational and logical penalties

Binary equality and inequalities are compiled into the same exact penalties as the QUBO path:

```python
model.add_constraint(x + y <= 1, label="capacity")
model.add_constraint(x + y >= 1, label="coverage")
model.add_constraint(x == z, label="link")
```

Logical gates are supported:

```python
from qpiai_opt.opt_modeller import AND, NOT, OR, XOR

a, b = x, y
a_and_b = Binary("a_and_b")
a_or_b = Binary("a_or_b")
a_xor_b = Binary("a_xor_b")
not_a = Binary("not_a")

model.add_constraint(AND(a, b, a_and_b))
model.add_constraint(OR(a, b, a_or_b))
model.add_constraint(XOR(a, b, a_xor_b))
model.add_constraint(NOT(a, not_a))
```

Automatic weights dominate the complete unconstrained energy range in either mode. With `strict_penalties=False` (default), an explicit hard-constraint weight below the computed safe threshold emits `UserWarning`: `Selected penalty may be insufficient. Recommended penalty weight: {x}.` Here `x` is the SDK-computed threshold. Compilation retains the selected weight; `strict_penalties=True` raises `ConstraintValidationError` instead. Python warning filters control display; treating this warning as an error aborts compilation and restores the preceding compilation state. `CustomPenalty(expression)` contributes energy but is not a hard constraint and receives no sufficiency warning.

HUBO compilation does not quadratize higher-order terms. It preserves them in the polynomial. Use `hubo_to_qubo()` when the consumer requires a quadratic model.

Modelling, local solving, and platform execution have separate capability boundaries. The native local solver has no separate polynomial-degree ceiling; term degree contributes to its deterministic work limit. The platform applies its current service and resource limits independently. A model above a target's limit remains valid for inspection, IR persistence, energy evaluation, or explicit conversion; solving through that target raises instead of truncating or reducing the term silently.

Expression multiplication rejects an operation that would require more than 100,000 intermediate term-and-factor operations, and a single canonical term may retain at most 100,000 factors. These are work and memory guards, not polynomial-degree capabilities. Build a sparse polynomial from explicit `Term` objects when an algebraic expansion would materialize unnecessary zero or duplicate terms.

## Direct polynomial functions

```python
from qpiai_opt.opt_modeller.formats import (
    calculate_hubo_energy,
    canonicalize_polynomial,
    get_polynomial_stats,
    polynomial_to_string,
    validate_polynomial_dict,
)

validate_polynomial_dict(polynomial)
canonical = canonicalize_polynomial(polynomial)
energy = calculate_hubo_energy(
    canonical,
    {"x": 1, "y": 0, "z": 1},
)
stats = get_polynomial_stats(canonical)
text = polynomial_to_string(canonical)
```

### Validation

`validate_polynomial_dict()` returns `None` after checking:

- the polynomial is a dictionary;
- every key is a tuple;
- coefficients are finite real values, excluding booleans;
- labels are hashable, non-boolean, and not empty strings.

Coefficients are stored as IEEE-754 binary64 values. Ordinary decimal rounding is expected, and `decimal.Decimal` is not accepted. Keep values within the finite binary64 range; the shared numeric contract is described in the [canonical IR reference](model-ir.md#numeric-representation).

### Canonicalization

`canonicalize_polynomial()` returns a new dictionary. It applies binary idempotence, orders factors within each term, aggregates equivalent terms, checks finite overflow, and removes zero totals. Monomial order follows first occurrence in the input.

### Energy

`calculate_hubo_energy()` requires a mapping containing every referenced label. Every referenced value must be `0` or `1`; missing values do not default to zero. Extra values are ignored because they do not occur in the polynomial.

### Statistics

`get_polynomial_stats()` returns:

| Field | Meaning |
| --- | --- |
| `num_variables` | Number of distinct labels. |
| `num_terms` | Number of nonzero canonical terms, including the constant if nonzero. |
| `degree` | Largest canonical term size; zero for an empty/constant-zero polynomial. |
| `constant` | Coefficient of `()`, or zero. |
| `term_counts` | Degree-to-term-count dictionary. |
| `variables` | Deterministically sorted labels. |

`polynomial_to_string()` returns a readable algebraic string. It is for inspection, not interchange parsing.

## Exact HUBO-to-QUBO reduction

```python
from qpiai_opt.opt_modeller import hubo_to_qubo

qubo_convert = hubo_to_qubo(
    polynomial,
    sense="MIN",
    matrix_format="symmetric",
)
```

The conversion introduces reusable auxiliary product variables. Each product receives a Rosenberg penalty that is zero exactly when the auxiliary equals the product.

The automatic `strength` is above the complete transformed objective range. Supplying a smaller value raises `ModelCompilationError`; the converter does not return a silently non-exact reduction.

Conversion fields:

| Field | Meaning |
| --- | --- |
| `qubo`, `offset` | Reduced QUBO minimization energy. |
| `variable_names` | All QUBO columns in index order. |
| `original_variables` | Source HUBO labels. |
| `auxiliary_products` | Auxiliary label to defining pair. |
| `strength` | Applied consistency strength. |
| `source_polynomial` | Canonical source polynomial. |
| `original_sense` | Validated source sense. |
| `index_to_variable`, `variable_to_index` | Explicit bidirectional QUBO map. |

Conversion methods:

```python
source_sample = {"x": 1, "y": 0, "z": 1}
full_sample = qubo_convert.complete_sample(source_sample)

auxiliaries_valid = qubo_convert.verify_auxiliaries(full_sample)
decoded_source_sample = qubo_convert.decode(full_sample)

qubo_energy = qubo_convert.qubo_energy(full_sample)
source_objective = qubo_convert.original_objective(source_sample)
```

`complete_sample()` fills missing auxiliaries from source values and returns a list in `variable_names` order. If a caller supplies auxiliary values, they are retained; call `verify_auxiliaries()` to check them.

For `sense="MAX"`, the QUBO minimizes the negative source polynomial. `original_objective()` reports the unnegated source value.

For a validated solver-facing `HUBOProblem`, the automation operation reads the sense from the problem and does not require it again:

```python
from qpiai_opt.opt_solvers import convert
from qpiai_opt.opt_solvers.hubo import HUBOProblem

hubo_problem = HUBOProblem.from_model(model)
qubo_convert = convert(
    hubo_problem,
    "QUBO",
    matrix_format="upper",
)
preserved_source_sense = qubo_convert.original_sense
```

The accepted options are `strength`, `auxiliary_prefix`, and `matrix_format`. Passing a second `sense` is rejected so it cannot conflict with `hubo_problem.sense`.

## Labels and ambiguity

Low-level HUBO dictionary utilities accept any valid hashable label except booleans and empty strings. This permits tuples and other hashable domain identifiers.

The solver-facing `HUBOProblem` is narrower: labels must be non-empty strings. This guarantees unambiguous JSON result keys. The empty tuple `()` is still the constant term. See the [HUBO solver guide](hubo-solver.md).

Lower-level modelling and conversion functions may still accept other hashable labels. Convert them to distinct strings before constructing a `HUBOProblem` or crossing a JSON boundary.

## Failure behavior

HUBO modelling and reduction reject:

- missing objectives and incompatible variable types;
- non-finite coefficients or aggregation overflow;
- malformed/unhashable labels;
- incomplete or non-binary energy samples;
- insufficient explicit hard-constraint weights when `strict_penalties=True`;
- insufficient explicit quadratization strengths in either mode;
- unsupported SOS/indicator constraints;
- continuous variables without an explicit resolution;
- reduction residue above degree two.

The sections above define the polynomial, statistics, reduction result, decode methods, and their validation failures.
