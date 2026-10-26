# MILP modeller

The MILP path represents linear objectives and linear constraints over continuous and discrete domains. It produces solver-neutral matrices and LP/MPS files; solving is handled separately by `qpiai_opt.opt_solvers.milp`.

## Mathematical form

```latex
$$
\begin{aligned}
\min\;\text{or}\;\max\quad &c^Tx+c_0\\
\text{subject to}\quad &b_l\le Ax\le b_u,\\
&l\le x\le u.
\end{aligned}
$$
```

Each column has a declared continuous or discrete domain. LP uses continuous columns; MILP permits integer and binary columns alongside continuous columns. Equal row bounds express equalities, and infinite bounds express one-sided rows or unbounded variable endpoints. Supported domain, indicator, and SOS reformulations produce this linear form.

## Complete model

```python
from qpiai_opt.opt_modeller import Binary, Continuous, Indicator, Integer, Model

produce = Integer("produce", 0, 8)
overtime = Continuous("overtime", 0, 10)
open_line = Binary("open_line")

model = Model("production")
model.minimize(5 * produce + 12 * overtime + 30 * open_line)
model.add_constraint(produce + overtime >= 10, label="demand")
model.add_constraint(produce <= 8 * open_line, label="activation")
model.add_constraint(
    Indicator(open_line, 1, produce >= 3),
    label="minimum_batch",
)

problem_class = model.validate("MILP")  # "MIP"
matrix = model.to_lp_matrix()
path = model.to_lp_file("production.lp")
```

An objective is required before validation or export. `minimize()` and `maximize()` accept a variable, an expression, or a finite real constant. Objective constants are preserved.

### Numeric inputs

The modeller stores numeric coefficients, bounds, and constants as IEEE-754 binary64 values. Ordinary decimal rounding is expected; values are not kept as arbitrary-precision decimals. Keep values within the finite binary64 range and scale a model with very large or very small quantities. Integer variable bounds are limited to `2**53 - 1` in magnitude. `decimal.Decimal` is not accepted; use a supported Python or NumPy real value. See [canonical IR numeric representation](model-ir.md#numeric-representation) for the shared representation rules.

## Variables and domains

| Constructor | Mathematical domain | Linear export |
| --- | --- | --- |
| `Continuous(name, lower_bound=0.0, upper_bound=inf)` | $x \in [lower\_bound, upper\_bound]$ | Continuous column. Infinite lower or upper bounds are allowed. |
| `Integer(name, lower_bound=0, upper_bound=None)` | $x \in \mathbb Z \cap [lower\_bound, upper\_bound]$ | Integer column. `None` means positive infinity. |
| `Binary(name)` | $x \in \{0,1\}$ | Binary column. |
| `SemiContinuous(name, lower_bound, upper_bound)` | $x=0$ or $lower\_bound \le x \le upper\_bound$ | Semi-continuous column or exact MILP representation. |
| `SemiInteger(name, lower_bound, upper_bound)` | $x=0$ or $x \in \mathbb Z \cap [lower\_bound,upper\_bound]$ | Semi-integer column or exact MILP representation. |
| `Fraction(name, lower_bound, upper_bound, precision_decimals=2)` | Fixed decimal grid in the closed interval | Exact scaled-integer reformulation. |
| `Mixed(name, valid_discrete_values, continuous_range)` | One listed discrete value or any real value in the interval | Exact disjunctive MILP reformulation. |

Requirements enforced at construction:

- names are non-empty single-line strings;
- finite integer and semi-integer bounds lie in `[-(2**53 - 1), 2**53 - 1]` and have integral values;
- semi-domain bounds are finite and satisfy `0 < lower <= upper`;
- fixed-point bounds are finite, aligned to 0 through 15 requested decimal places, and safe to scale exactly;
- mixed discrete values are finite and unique, and the continuous interval is finite and ordered.

Two distinct variable objects may use the same name in different models. A single model rejects distinct variables with the same name. Use `model.add_variable(variable)` or `add_variables(...)` to retain an isolated or currently zero-coefficient variable:

```python
reserve = Continuous("reserve", 0, 5)
model.add_variable(reserve)
```

`model.variables()` returns the model's variables in stable first-use order.

## Expressions

Variables support:

- addition and subtraction;
- multiplication by a finite scalar;
- division by a nonzero finite scalar;
- variable products and non-negative integer powers;
- `<=`, `>=`, and `==` to construct modelling constraints.

The MILP validator accepts objectives and relational bodies of degree at most one. Nonlinear expressions use the QUBO/HUBO or QP paths; linear export rejects them.

Use `quicksum()` instead of repeated Python addition for a large collection:

```python
from qpiai_opt.opt_modeller import create_binary_array, dot, quicksum

select = create_binary_array("select", 100)
cost = dot([2.0] * 100, select)
cardinality = quicksum(select)
```

`dot(coefficients, variables)` requires equal lengths.

### Direct expression objects

Operator syntax is the usual entry point, but the exported low-level constructors are also supported:

```python
from qpiai_opt.opt_modeller import Expression, Term

direct = Expression(
    terms=[
        Term(5, [produce]),
        Term(12, [overtime]),
    ],
    constant=30,
)
same = Expression.from_terms(direct.terms, constant=direct.constant)
```

`Term(coefficient, variables)` requires a finite real coefficient and decision variable factors. `Expression(terms=None, constant=0)` combines such terms and a finite constant; constant-only terms are folded into the constant and zero-coefficient terms are removed.

`expression.terms` returns a list of terms. `expression.degree()` returns the polynomial degree. Pass `raise_on_higher_order=True` when a quadratic-only operation should reject a higher-order expression with `NonlinearExpressionError`. `expression.evaluate(values)` accepts a complete variable/name mapping, and `expression.to_readable_string()` returns display text. Display text is not an interchange parser. `expression.to_terms()` returns a defensive list copy of its terms.

`model.objective_as_string()` returns the objective display text. `model.print_objective()` prints and returns that same string; neither method is an interchange or serialization format.

## Relational constraints

```python
model.add_constraint(produce + overtime <= 12, label="capacity")
model.add_constraint(produce - overtime >= 4, label="balance")
model.add_constraint(produce + 2 * overtime == 12, label="recipe")
```

`add_constraint()` returns the same constraint. An explicit `label` must be a non-empty unique string. When omitted, the model uses `c0`, `c1`, and so on. Generated and explicit effective names cannot collide.

`penalty_weight` is stored for QUBO/HUBO compilation. It has no effect on native linear export. It must be a finite positive number when supplied.

## Indicator constraints

`Indicator(trigger, active_value, body)` enforces `body` only when the binary trigger equals `active_value`.

```python
from qpiai_opt.opt_modeller import Indicator

model.add_constraint(
    Indicator(open_line, 0, overtime == 0),
    label="closed_line_has_no_overtime",
)
```

The trigger must be `Binary`, the active value must be `0` or `1`, and the body must be a linear equality or inequality. Export derives a tight big-M value from body-variable bounds. Every body variable must therefore have finite bounds. Equality indicators become one upper and one lower row.

The active case is checked for floating-point cancellation during export. If the reformulated row differs from the source relation by more than the SDK's `1e-6` source-feasibility tolerance, export raises a numerical-stability error. Tighten the body-variable bounds or rescale the model before exporting; a weakened active relation is never silently accepted.

Indicator constraints are not accepted by QUBO/HUBO compilation.

## SOS1 and SOS2

```python
from qpiai_opt.opt_modeller import Continuous, Model, SOS1, SOS2, create_binary_array

modes = create_binary_array("mode", 3)
interpolation_weights = [Continuous(f"weight_{i}", 0, 1) for i in range(4)]
sos_model = Model("special_ordered_sets")
sos_model.minimize(0)
sos_model.add_constraint(SOS1("one_mode", modes))
sos_model.add_constraint(
    SOS2(
        "adjacent_breakpoints",
        interpolation_weights,
        weights=[0, 10, 20, 30],
    )
)
```

- SOS1 permits at most one nonzero member.
- SOS2 permits at most two nonzero members; two active members must be adjacent after ordering by weight.
- A set needs at least one member. Member names cannot repeat.
- Weights default to `1, 2, ...`; explicit weights must be finite, unique, and match the member count.

`to_lp_matrix()`, LP, and MPS use exact selector reformulations by default. Selector reformulations require finite member bounds; an SOS2 set with at most two members needs no selectors or finite bounds. Pass `for_milp=False` to retain native SOS records instead:

```python
native_sos_data = sos_model.to_lp_matrix(for_milp=False)
sos_reformulated = native_sos_data["sos_reformulated"]  # False
```

## Model validation and classification

```python
classification = model.validate()
```

Without a target, the model is classified from its variables, objective degree, and constraints. Linear results are:

- `"LP"` for continuous-only linear models;
- `"MILP"` for discrete-only linear models;
- `"MIP"` when continuous and discrete variables occur together.

`validate("LP")`, `validate("MILP")`, and `validate("MIP")` all run the same linear structural validation and return the normalized classification. A requested target does not relax domain or linearity rules.

## Matrix representation

```python
data = model.to_lp_matrix(
    for_milp=True,
    sparse_matrix=True,
)
```

The central numerical contract is:

```text
row_lower <= constraint_matrix @ x <= row_upper
objective value = objective @ x + objective_offset
```

`variable_names` defines column order; `constraint_names` defines row order. `lower_bounds` and `upper_bounds` align with columns. `integrality` uses SciPy codes:

| Code | Domain |
| ---: | --- |
| `0` | continuous |
| `1` | integer, including binary selectors |
| `2` | semi-continuous |
| `3` | semi-integer |

The default constraint matrix is SciPy CSR. Set `sparse_matrix=False` only when a consumer requires a dense NumPy array.

Local linear and quadratic solves require `1e-9 < abs(value) < 1e15` for every nonzero constraint-matrix or Hessian coefficient. Objective coefficients and finite bounds have separate [numeric limits](local-solving.md#numeric-input-range). Rescale values outside these ranges before solving.

Domain and indicator reformulations can add auxiliary columns and rows in either mode. `for_milp=True` also reformulates SOS sets; `False` retains them separately. Read original solution values from their named columns. Both options require Python `bool` values.

The result is a `dict` with the following fields. Let `n` be the number of exported columns and `m` the number of exported linear rows, including reformulations.

| Field | Type and meaning |
| --- | --- |
| `name` | `str`: source model name. |
| `problem_type` | `"LP"`, `"MILP"`, or `"MIP"`: source linear classification. |
| `solver` | `str`: linear adapter identifier, `"milp"`. |
| `format` | `"matrix"`. |
| `sense` | `"min"` or `"max"`; coefficients retain the source direction. |
| `variable_names` | `list[str]`, length `n`: column names. |
| `objective` | NumPy `float64` array, shape `(n,)`: linear objective coefficients. |
| `objective_coefficients` | `dict[str, float]`: nonzero linear coefficients by variable name. |
| `objective_offset` | `float`: source objective constant. |
| `constraint_names` | `list[str]`, length `m`: linear row names. |
| `constraint_matrix` | SciPy CSR `float64` matrix of shape `(m, n)`; a NumPy `float64` array with the same shape when `sparse_matrix=False`. |
| `row_lower`, `row_upper` | NumPy `float64` arrays, shape `(m,)`: row activity bounds; infinities indicate absent bounds. |
| `constraints` | `list[dict]`, length `m`: structured row records described below. |
| `lower_bounds`, `upper_bounds` | NumPy `float64` arrays, shape `(n,)`: column bounds, with infinities where allowed. |
| `integrality` | NumPy `int32` array, shape `(n,)`: domain codes in the table above. |
| `variable_types` | `dict[str, str]`: exported column domain kinds. Fractional and mixed source variables use continuous columns plus explicit domain reformulations. |
| `sos` | `list[dict]`: native SOS records when `for_milp=False`; otherwise empty. |
| `sos_reformulated` | `bool`: the supplied `for_milp` setting, even when the model contains no SOS sets. |

Each `constraints` record has `name: str`, `sense: "=" | "<=" | ">="`, `rhs: float`, `lower: float`, `upper: float`, `coefficients: dict[str, float]`, and `is_sos=False`. Bounds and coefficients describe the exported row after constants have moved to the right-hand side.

Each native `sos` record has `name: str`, `type: "S1" | "S2"`, `variables: list[str]`, and `weights: list[float]` in corresponding order. Native SOS records are additional constraints; a consumer must enforce them as well as the linear matrix. Keeping only the matrix would lose those restrictions.

## LP, MPS, and gzip export

```python
lp_text = model.to_lp_string()
mps_text = model.to_mps_string()

model.to_lp_file("model.lp")
model.to_lp_file("model.lp.gz")
model.to_mps_file("model.mps")
model.to_mps_file("model.mps.gz")
```

The writers preserve:

- objective sense and constant;
- row names, senses, and right-hand sides;
- variable bounds and integrality;
- native SOS records when `for_milp=False`;
- exact MILP reformulations when `for_milp=True`.

Variable, row, and SOS names are checked against a portable LP/MPS subset: at most 255 ASCII characters, beginning with a letter, `_`, `.`, or `$`, followed by letters, digits, `_`, `.`, `$`, `[`, `]`, or `,`. Format keywords and names beginning with `inf` case-insensitively are rejected. LP encodes square brackets as parentheses and includes a naming-convention header; MPS retains square brackets. Unsupported names fail before a file is opened. Long LP expressions wrap below the portable line-length limit.

String exporters return newline-terminated text. File exporters accept a string or path-like destination and return its string path. Suffix checks are case-insensitive and require `.lp`/`.lp.gz` or `.mps`/`.mps.gz` respectively. Files use UTF-8 and LF newlines; `.gz` requests gzip compression. Existing files are overwritten after content validation. Parent directories must already exist, and filesystem errors propagate.

A constant-only model is valid. Its in-memory matrix has zero variables. LP/MPS text represents it with one fixed zero-valued column so standard readers retain the objective constant.

## Strict dictionary interchange

`generate_lp()` converts a linear dictionary into both LP text and matrix data:

```python
from qpiai_opt.opt_modeller.formats import generate_lp

lp_text, matrix_data = generate_lp(
    {
        "name": "dictionary_model",
        "objective": {
            "sense": "max",
            "terms": {"open": 20, "units": 9},
            "constant": -5,
        },
        "constraints": [
            {
                "name": "capacity",
                "terms": {"units": 1, "open": -8},
                "sense": "<=",
                "rhs": 0,
            }
        ],
        "bounds": {"units": [0, 8]},
        "binary": ["open"],
        "general": ["units"],
    }
)
```

Top-level fields:

| Field | Required | Value |
| --- | --- | --- |
| `name` | no | Non-empty model name; defaults to `GeneratedLP`. |
| `objective` | yes | Dictionary: optional `sense="min"` (case-insensitive `min`/`max`), `terms={}` (name-to-coefficient dictionary), and `constant=0.0`. |
| `constraints` | no | List or tuple of dictionaries: required `sense` (`=`, `<=`, or `>=`) and finite `rhs`; optional `terms={}` and `name=None`. Missing names become `c0`, `c1`, and so on. |
| `bounds` | no | Name-to-pair dictionary; each pair is a list or tuple `[lower, upper]`. `None` lower means negative infinity; `None` upper means positive infinity. |
| `binary`, `general` | no | List, tuple, or set of variable names, deduplicated within each section. |
| `semi_continuous`, `semi_integer` | no | The same name collections; each variable requires explicit finite bounds with `0 < lower <= upper`. |
| `sos` | no | List or tuple of dictionaries with required `name`, `type` (`S1`/`S2`), and nonempty `vars` mapping variable names to finite unique weights. |

Variables not listed in a type section are continuous. Type sections cannot overlap. Every dictionary level rejects unknown fields, so a misspelled `constraints`, `sense`, or type section cannot silently change the model. Coefficients and right-hand sides must be finite.

Undeclared continuous/integer bounds default to `[0, +inf]`; binary bounds default to `[0, 1]`. Explicit binary bounds must be `[0, 1]`, `[0, 0]`, or `[1, 1]`; a fixed binary is enforced by an added equality. Variables are collected from nonzero objective/constraint terms, bounds, type declarations, and SOS membership, then registered in sorted name order. A zero coefficient alone does not declare a variable. At least one collected variable is required. These dictionary defaults are separate from a variable constructor's defaults.

## Evaluate and verify a solution

```python
solution = {
    "produce": 8,
    "overtime": 2.0,
    "open_line": 1,
    "reserve": 0.0,
}

objective = model.evaluate_objective(solution)
violations = model.constraint_violations(solution)
feasible = model.is_feasible(solution)
```

The default `1e-6` tolerance matches local linear-solver feasibility. For a linear constraint, the threshold is scaled by the larger of one, the row activity, and the row bound. Variable-domain and SOS checks use the tolerance directly. Supply `tolerance=...` when your application requires a different verification threshold.

The assignment must contain every source variable. Validation covers:

- finite real values;
- continuous bounds;
- binary, integer, and fixed-point membership;
- zero-or-interval semi-domains;
- mixed-domain unions;
- equality and inequality residuals;
- active indicators;
- SOS adjacency/cardinality;
- logical constraints if the model contains them.

Domain errors use `variable:<name>` keys. Constraint entries are zero when satisfied within tolerance.

## Supported boundary

LP/MILP export requires linear objectives and rows. Solver configuration, status, basis information, and IIS data are documented in [the MILP solver guide](milp-solver.md). To convert a bounded linear discrete model to QUBO, use `milp_to_qubo()` as described in the [QUBO modeller guide](qubo-modeller.md#milp-to-qubo).

Invalid structures raise typed exceptions from `qpiai_opt.opt_modeller.utils.exceptions`; [Automation failure handling](../automation.md#failure-handling) explains recovery at workflow level.
