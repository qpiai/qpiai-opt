# QUBO modeller

A QUBO is a quadratic polynomial over binary variables. The SDK compiles source models to a minimization energy and supports two exactly equivalent matrix representations.

## Mathematical form

The fully symmetric form is the default:

```latex
$$
\begin{aligned}
E(x)&=c+x^TQx\\
    &=c+\sum_i Q_{ii}x_i+2\sum_{i<j}Q_{ij}x_ix_j.
\end{aligned}
$$
```

`c` is the constant offset and each `x[i]` is binary. Symmetric storage contains both `(i, j)` and `(j, i)`, with equal values. If the direct polynomial coefficient of `x[i] * x[j]` is `w`, both matrix positions store `w / 2`. Diagonal coefficients are unchanged.

The upper-triangular form stores direct polynomial terms:

```latex
$$
E(x)=c+\sum_{i\le j}W_{ij}x_ix_j.
$$
```

It contains no lower-triangle entries. The QpiAI QUBO solver expects the fully symmetric form, so platform-facing APIs use it by default.

## Unconstrained model

```python
from qpiai_opt.opt_modeller import Binary, Model

x = Binary("x")
y = Binary("y")

model = Model("selection")
model.minimize(-3 * x - 2 * y + 4 * x * y + 7)

qubo, offset = model.to_qubo_dict()
compiled_matrix = qubo
compiled_offset = offset  # 7.0
column_names = model.qubo_variable_names  # ["x", "y"]
```

For this expression, symmetric output contains `(0, 1): 2` and `(1, 0): 2`; upper output contains `(0, 1): 4`.

```python
upper_qubo, upper_offset = model.to_qubo_dict(matrix_format="upper")
```

Use `"symmetric"` or `"upper"` as the representation name.

## Compilation output and column mapping

The lower-level compiler returns NumPy triplets:

```python
rows, columns, coefficients, offset = model.to_qubo_triplets(
    matrix_format="symmetric",
)
```

All arrays are one-dimensional and equal in length. Each index addresses the latest `model.qubo_variable_names`:

```python
index = model.qubo_index("x")
index_to_name = model.qubo_variable_names[index]  # "x"
name_to_index = model.qubo_variable_indices["x"]  # 0
```

Indices are compact and model-local. They are not `Variable.index`, object IDs, or stable IDs across a different compilation. Generated encodings and constraint auxiliaries can change the compiled column set when model structure or compile options change.

Every source variable survives decoding, including an isolated declared variable or a variable appearing only in a redundant source constraint.

## Source sense and offset

QUBO solvers minimize. A source maximization is negated during compilation:

```python
model.maximize(3 * x + 2 * y - 4 * x * y + 5)
qubo, offset = model.to_qubo_dict()
```

The compiled `qubo` and `offset` describe the minimizing solver energy, not the source maximization objective. Recover a source value with:

```python
from qpiai_opt.opt_modeller.formats import (
    calculate_qubo_energy,
    calculate_true_objective,
)

solver_energy_without_offset = calculate_qubo_energy(qubo, [1, 0])
source_value = calculate_true_objective(
    solver_energy_without_offset,
    offset,
    model.sense,
)
```

For platform submission, prefer `QUBOProblem.from_model(model)`. It carries sense and offset metadata and the high-level solver restores `result.objective` automatically.

`QUBOProblem.from_model(model)` compiles an independent snapshot of the source model, including its objective, constraints, encodings, and transformations. Later edits to `model` do not affect the compiled problem's decoding or result interpretation. Compile a new problem when the next solve should include those edits.

## Relational constraints

Before allocating slack, the compiler recognizes exact binary implication, at-most-one, and at-least-`n-1` structures. These exhaustive-domain identities introduce no auxiliary variables:

```python
a, b, c = Binary("a"), Binary("b"), Binary("c")
model = Model("small_penalty")
model.minimize(-a - b - c)
model.add_constraint(a + b + c <= 1)
model.to_qubo_dict(matrix_format="upper")

step = model.transformations()[-1]
rewrite = step["rewrite"]
auxiliary_variables = step["auxiliary_variables"]  # []
```

The exact identities and inspection records are listed in [Transformations](transformations.md#specialized-exact-qubo-penalties). Unrecognized constraints continue through the generic exact slack path.

```python
model.add_constraint(x + y <= 1, label="capacity")
model.add_constraint(x + y >= 1, label="coverage")
model.add_constraint(x == a, label="link")
```

Compilation adds non-negative exact penalties:

- equality uses a squared residual;
- inequality introduces bounded binary slack on the expression's rational coefficient lattice;
- the bounded slack encoding covers the feasible residual range on that lattice.

Automatic penalty selection strictly separates infeasible states from the complete unconstrained objective range. It includes all source objective terms and `CustomPenalty` contributions.

An explicit penalty must be finite and positive. Strict rejection can be requested:

```python
model.add_constraint(x + y <= 1, penalty_weight=20)
model.to_qubo_triplets(strict_penalties=True)
```

With `strict_penalties=False` (default), an explicit hard-constraint weight below the computed safe threshold emits `UserWarning`: `Selected penalty may be insufficient. Recommended penalty weight: {x}.` Here `x` is the SDK-computed threshold. Compilation retains the selected weight; the warning does not increase it. `strict_penalties=True` raises `ConstraintValidationError` instead. Omitted weights still use automatic safe selection in either mode. A smaller explicit weight can allow infeasible low-energy assignments, so verify source feasibility before using a result. Python warning filters control warning display; treating this warning as an error aborts compilation and restores the preceding compilation state.

`model.estimate_penalty_weight(constraint, **compile_options)` uses the compiler's encoding and automatic-weight calculation on an independent snapshot. Set the objective first, then supply a constraint object, label, or index; an unattached object is included in the snapshot. Encoding options match the relevant QUBO/HUBO export. A redundant constraint returns `0.0`; selecting a `CustomPenalty` raises `ConstraintValidationError`. Unsupported encodings and numerical failures raise the compiler's existing errors.

## Logical constraints

```python
from qpiai_opt.opt_modeller import AND, NOT, OR, XOR

a_and_b = Binary("a_and_b")
a_or_b = Binary("a_or_b")
a_xor_b = Binary("a_xor_b")
not_a = Binary("not_a")

model.add_constraint(AND(a, b, a_and_b), label="and_gate")
model.add_constraint(OR(a, b, a_or_b), label="or_gate")
model.add_constraint(XOR(a, b, a_xor_b), label="xor_gate")
model.add_constraint(NOT(a, not_a), label="not_gate")
```

All arguments must be distinct `Binary` variables within each gate.

- AND, OR, and NOT have exact quadratic penalties over their public variables.
- XOR uses one deterministic binary auxiliary because no non-negative quadratic penalty over only its three public variables represents XOR exactly.

`CustomPenalty(expression, label=None)` adds the expression directly to the energy. It is not a hard-constraint declaration and is not included in feasibility violations.

### Inspect generated penalties

`variables()` returns the variables used by a logical relation. `to_penalty()` returns its exact polynomial without adding it to a model.

```python
gate = XOR(a, b, a_xor_b)
penalty = gate.to_penalty()

gate_variables = [variable.name for variable in gate.variables()]
penalty_text = penalty.to_readable_string()
```

`AND.to_penalty()`, `OR.to_penalty()`, and `NOT.to_penalty()` are non-negative quadratic expressions that equal zero exactly when the relation holds. `XOR.to_penalty()` also includes the gate's binary auxiliary. Minimizing over that auxiliary gives zero exactly for a valid XOR relation; a supplied auxiliary value can give a positive penalty even when the source relation holds.

`CustomPenalty.to_penalty()` returns the supplied expression unchanged:

```python
from qpiai_opt.opt_modeller import CustomPenalty

soft_balance = CustomPenalty((a + b - 1) ** 2, label="balance")
soft_penalty_text = soft_balance.to_penalty().to_readable_string()
```

SOS1, SOS2, and `Indicator` are not converted to QUBO. They require the linear MILP path and raise if QUBO compilation is requested.

## Variable encoding

Binary variables need no encoding. These source domains are supported:

| Source domain | QUBO behavior |
| --- | --- |
| Bounded `Integer` | Exact offset plus bounded binary weights. |
| `Fraction` | Exact fixed-point grid. |
| Bounded `Continuous` | Explicit finite discretization; approximate except for represented grid points. |
| `SemiContinuous`, `SemiInteger`, `Mixed` | Unsupported by QUBO compilation. |
| `Spin` | Rejected by QUBO compilation; use native Ising or explicit conversion. |

### Integer

```python
from qpiai_opt.opt_modeller import Integer

quantity = Integer("quantity", -3, 5)
```

Both bounds must be finite for QUBO. The encoding represents every integer in the interval and no value above the upper bound. Fixed variables use no bits.

`encode_integer_binary(quantity)` returns an `Expression` with `.encoding` metadata:

- `source`, `kind`, `binaries`, `weights`, and `offset`;
- `absolute_error = 0`;
- `declared_bounds`, recording the interval encoded by this expression.

### Fixed-point

```python
from qpiai_opt.opt_modeller import Fraction

price = Fraction("price", 0.10, 1.50, precision_decimals=2)
```

The decimal grid is exact. `encode_fraction(price)` returns the encoded expression and metadata, including step and zero absolute error.

### Continuous

The model compiler requires an explicit discretization:

```python
from qpiai_opt.opt_modeller import Continuous

flow = Continuous("flow", 0, 1)
continuous_model = Model("flow_target")
continuous_model.minimize((flow - 0.5) ** 2)
qubo, offset = continuous_model.to_qubo_dict(continuous_bits=8)
```

This represents `2**8` evenly spaced levels including both bounds. The maximum rounding error recorded in `encoding_map` is half the step.

Alternatively:

```python
qubo, offset = continuous_model.to_qubo_dict(continuous_precision=2)
```

This uses step `10**-2` starting at the variable's lower bound. The interval width must be an integer multiple of that step; the lower bound need not itself be a multiple of the step. Bounded binary weights exclude levels above the upper endpoint.

Do not pass both settings. `max_bits` defaults to 20 and must be from 1 through 53. It prevents an accidental encoding larger than intended.

The direct helper `encode_continuous(variable, num_bits=None, precision=None, max_bits=20)` defaults to eight bits only when called directly. `Model.compile` does not choose a continuous resolution silently.

Compiling again uses the current bounds and selected resolution. A previously compiled problem retains its original encoding snapshot.

## Decode and verify a sample

```python
decode_model = Model("decode_selection")
decode_model.minimize(-3 * x - 2 * y + 4 * x * y + 7)
decode_model.to_qubo_dict()
binary_sample = [1, 0]

decoded = decode_model.decode_qubo_sample(binary_sample)
source_objective = decode_model.evaluate_objective(decoded)  # 4.0
violations = decode_model.constraint_violations(decoded)
feasible = decode_model.is_feasible(decoded)  # True
```

Accepted samples:

- sequence in `qubo_variable_names` order;
- dictionary containing every integer index;
- dictionary containing every compiled variable name;
- a mixed named/indexed dictionary when duplicate representations agree.

Values must be binary. Missing or conflicting entries raise. The decoded dictionary contains source variable names and reconstructed values; compiler auxiliaries are omitted.

`evaluate_objective()` evaluates the original source expression. `is_feasible()` checks source domains and constraints.

## QUBO format functions

```python
from qpiai_opt.opt_modeller.formats import (
    calculate_qubo_energy,
    format_qubo_triplets,
    to_qubo_dict,
    to_scipy_sparse,
)
```

| Function | Output |
| --- | --- |
| `format_qubo_triplets` | Canonical triplets in symmetric or upper form. |
| `to_qubo_dict` | Canonical `(row, column)` dictionary. |
| `to_scipy_sparse` | Choose symmetric or upper coefficients and CSR or COO storage independently. |
| `calculate_qubo_energy` | Energy for a binary sample exactly as the supplied dictionary is represented. |

Triplet functions:

- accept lower- or upper-pair input and canonicalize it;
- aggregate duplicate terms without making the result depend on input order;
- eliminate exact zeros;
- reject non-integer/out-of-range indices and non-finite coefficients;
- reject an off-diagonal split that underflows to zero.

`calculate_qubo_energy()` does not infer whether a dictionary is symmetric or upper; it sums every stored pair. A correct symmetric dictionary therefore stores half coefficients in both directions.

## HUBO to QUBO

```python
from qpiai_opt.opt_modeller import hubo_to_qubo

qubo_convert = hubo_to_qubo(
    {
        (): 3,
        ("x",): -2,
        ("x", "y", "z"): 5,
    }
)
```

The converter reuses common product pairs and applies exact Rosenberg product penalties. A safe strength is computed above the complete transformed objective range. `strength=...` may be supplied but is rejected if below that minimum.

Important outputs:

```python
qubo_convert.qubo
qubo_convert.offset
qubo_convert.index_to_variable
qubo_convert.variable_to_index
qubo_convert.auxiliary_products

full_sample = qubo_convert.complete_sample({"x": 1, "y": 0, "z": 1})
source_sample = qubo_convert.decode(full_sample)
auxiliaries_valid = qubo_convert.verify_auxiliaries(full_sample)
```

Original labels remain intact, including non-string hashable labels accepted by the modeller converter. Integer labels can conflict with QUBO index keys; an ambiguous dictionary raises, and a sequence in `variable_names` order resolves the ambiguity.

## QUBO to MILP

```python
from qpiai_opt.opt_modeller import qubo_to_milp

milp_convert = qubo_to_milp(
    {(0, 0): -3, (0, 1): 4, (1, 1): -2},
    num_variables=2,
    variable_names=("x", "y"),
    constant=7,
)

milp_model = milp_convert.model
```

The conversion linearizes each product `x[i] * x[j]` with an auxiliary binary variable and the exact three McCormick constraints for binary factors. `milp_convert.decode(solution)` removes product variables.

The input dictionary is interpreted as direct polynomial terms: mirrored pairs are aggregated. Do not pass a symmetric matrix with full rather than half-valued mirrored entries.

## MILP to QUBO

```python
from qpiai_opt.opt_modeller import milp_to_qubo

linear_model = Model("linear_selection")
linear_model.minimize(-3 * x - 2 * y + 7)
qubo_convert = milp_to_qubo(linear_model)
qubo = qubo_convert.qubo
decoded = qubo_convert.decode(binary_sample)
```

Supported source structures are the same as `Model.to_qubo_dict()`: linear objectives and relations over binary, bounded integer, fixed-point, and explicitly discretized bounded continuous variables. SOS, indicators, semi-domains, and mixed domains are rejected.

The conversion exposes `offset`, `variable_names`, `index_to_variable`, `variable_to_index`, `encoding_map`, `original_sense`, and the source `model`.

## QUBO and Ising

```python
from qpiai_opt.opt_modeller import ising_to_qubo, qubo_to_ising

ising_convert = qubo_to_ising(
    upper_qubo,
    num_variables=len(column_names),
    constant=upper_offset,
    variable_names=tuple(column_names),
)

qubo_convert = ising_to_qubo(
    ising_convert.linear,
    ising_convert.quadratic,
    num_variables=ising_convert.num_variables,
    constant=ising_convert.offset,
    variable_names=ising_convert.variable_names,
)
```

The exact substitutions are `x = (1 + s) / 2` and `s = 2x - 1`. Constants and variable counts are preserved, including when several source terms contribute to the same field, coupling, diagonal, or offset. See the [Ising modeller](ising-modeller.md) for the conversion methods and native spin path.

## Numerical and structural failures

QUBO compilation rejects:

- non-finite or overflowing coefficients and offsets;
- an objective above quadratic degree;
- unsupported constraint types or domains;
- insufficient explicit hard-constraint penalties when `strict_penalties=True`;
- continuous models without an explicit resolution;
- an encoding beyond `max_bits`;
- duplicate source names in one model;
- invalid matrix formats and representation underflow.

Import modeller exceptions from `qpiai_opt.opt_modeller.utils.exceptions`. The [modeller API reference](api-modeller.md) defines the conversion result fields and decoding contracts.

## Compile an executable problem

`model.compile()` validates the detected class and returns `QUBOProblem`, `HUBOProblem`, `IsingProblem`, or `MILPProblem`. LP uses `MILPProblem` with `problem_type="LP"`. Continuous convex QP retains a validated independent `Model`, its existing native local-solver representation. `model.compile(target="QUBO", **encoding_options)` requests a supported representation; validation, encoding, penalties, numerical checks, and packaging must all succeed. Failure restores the preceding compilation state.

Compiled problems retain independent source snapshots, including variable encodings, auxiliaries, source sense, and transformation records. `solve(compiled_problem, client=client)` restores the source objective and named assignment even if the original model is subsequently changed. Raw coefficient exports remain available through `to_qubo_dict()`, `to_qubo_triplets()`, `to_hubo_dict()`, and `to_ising_dict()`.

`MILPProblem` lives in `qpiai_opt.opt_solvers.milp` and accepts exactly one of `model=Model(...)` or `model_path=...`, plus optional `config=MILPSolverConfig(...)`. In-memory models remain canonical snapshots; platform submission exports a temporary LP for the upload operation. Local execution accepts the existing time-limit, node-limit, and relative-gap controls. Explicit platform-only configuration fields raise in local mode.

## Binary quadratic expression arrays

`coeffs, var_indices, constant = expression.to_binary_quadratic_arrays()` returns detached arrays for binary expressions of degree at most two. Index rows contain opaque `Variable.index` identities; `-1` marks an absent factor. An explicitly constant-only expression has empty arrays. Stored terms can repeat or cancel algebraically, so these arrays are not a unique-monomial dictionary or compact QUBO coordinates. Higher-order and nonbinary expressions raise `InvalidExpressionError`; use `to_terms()` and `.constant` for them.
