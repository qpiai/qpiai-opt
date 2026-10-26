# Modeller API reference

Import modelling symbols from `qpiai_opt.opt_modeller`, except where a more specific import path is stated. The modelling chapters explain mathematical conventions and workflows; this reference gives signatures, input contracts, return values, and failures.

```python
from qpiai_opt.opt_modeller import Binary, Model, quicksum
```

## Class: `Model`

```text
Model(name='Model')
```

A model container for LP/MILP, continuous QP, QUBO, HUBO, and Ising workflows.

### Public state

| Attribute | Type | Meaning |
| --- | --- | --- |
| `name` | `str` | Non-empty single-line model name. |
| `objective` | `Expression \| None` | Current source objective, or `None` until `minimize()` or `maximize()` is called. |
| `constraints` | `list[dict]` | Ordered records with `constraint`, `weight`, and `label` keys. `weight` and `label` can be `None`. |
| `parameters` | `dict[str, Parameter]` | Registered parameters keyed by their unique names. |
| `sense` | `"MIN" \| "MAX"` | Source objective direction. Assigning another value raises `ObjectiveError`. |
| `qubo_variable_names` | `list[str]` | Variable order produced by the latest successful QUBO compilation. |
| `qubo_variable_indices` | `dict[str, int]` | Name-to-index map for that QUBO order. Prefer `qubo_index()` for validated lookup. |
| `hubo_variable_names` | `list[str]` | Variable order produced by the latest successful HUBO compilation. |
| `encoding_map` | `dict` | Latest successful domain-encoding map used by sample decoding. |
| `transformation_metadata` | `list[dict]` | Current modelling and compilation transformation records. Prefer `transformations()` when the caller needs a detached copy. |

Compilation replaces the compiled-state attributes only after the complete operation succeeds. Treat them as inspection and decoding state: generated auxiliary names and compiler layouts are not a persistence contract.

### Methods and properties

#### Method: `minimize`

```text
minimize(expression)
```

Set `sense="MIN"` and return the stored `Expression`. Accepts an `Expression`, a `Variable`, or a finite non-Boolean real constant. An existing expression is retained by reference; a variable or constant is converted to an expression. Invalid inputs raise `ObjectiveError`.

#### Method: `maximize`

```text
maximize(expression)
```

Set `sense="MAX"` and return the stored `Expression`. Inputs and reference semantics match `minimize()`; changing direction does not negate the stored expression.

#### Method: `add_variable`

```text
add_variable(variable)
```

Register a `Variable`, including a variable with no current objective or constraint term, and return that same object. Registering the same object again is harmless. A distinct variable with an existing name raises `ModelValidationError`.

#### Method: `add_variables`

```text
add_variables(variables)
```

Accept an iterable of `Variable` objects and return its materialized `list[Variable]`, retaining input order and repetitions. Registration itself deduplicates object identities. The entire batch is checked before registration; a non-variable member or a name shared by distinct source variables raises `ModelValidationError` without registering a partial batch.

#### Method: `add_indexed_variables`

```text
add_indexed_variables(indexed_variables)
```

Accept an `IndexedVariables` container, register its members with `add_variables()`, and return the same container. Other input types raise `ModelValidationError`.

#### Method: `set_objective_coefficient`

```text
set_objective_coefficient(variable_or_name, coefficient)
```

Replace the linear term for a model variable identified by its object or name. Other terms, the objective constant, and source sense are retained. A finite non-Boolean real `coefficient` returns its normalized `float`; zero removes the linear term while retaining the variable in the model. A `Parameter` binds its current and subsequent model-managed values and returns that parameter. The model must already have an objective. Unknown variables raise `ModelValidationError`; invalid coefficients raise `ObjectiveError`.

#### Method: `set_constraint_rhs`

```text
set_constraint_rhs(constraint, value)
```

Identify a stored constraint by its zero-based non-Boolean integer index or effective label. Replace its entire right-hand expression with the finite scalar `value`, retaining its left-hand expression and relation. Indicator constraints update their enclosed relation. A numeric value returns `float`; a `Parameter` creates a binding and returns the parameter. Unknown references raise `ModelValidationError`; non-relational constraints or invalid numeric values raise `ConstraintValidationError`.

#### Method: `set_variable_bounds`

```text
set_variable_bounds(variable_or_name, *, lower_bound=None, upper_bound=None)
```

Resolve a model variable by object or name and return that same variable after updating its bounds. `None` retains the corresponding current bound; it does not request infinity. Both bounds are validated together using the variable's constructor rules before either is changed. Supported types are `Integer`, `Continuous`, `SemiContinuous`, `SemiInteger`, and `Fraction`; fractional precision is retained. Binary, spin, and mixed domains reject bound updates with `ModelValidationError`.

#### Method: `add_parameter`

```text
add_parameter(parameter)
```

Accept and return a `Parameter`. Re-registering the same object is harmless; another object with the same name, or a non-parameter argument, raises `ModelValidationError`.

#### Method: `bind_parameter`

```text
bind_parameter(parameter, *, target, variable=None, constraint=None)
```

Accept a `Parameter` or the name of an already registered parameter. `target` is one of `"objective_coefficient"`, `"constraint_rhs"`, `"lower_bound"`, or `"upper_bound"`. Supply `variable` as a model variable or its name for coefficient/bound targets; supply `constraint` as its index or effective label for an RHS target. The current value is applied immediately. A failed binding removes any registration made by this call; duplicate bindings are retained only once.

Returns a detached `dict` with `parameter` and `target` strings, plus either `variable` as a name or `constraint` as an integer index. Unknown targets or references raise `ModelValidationError`; field validation uses the corresponding setter's errors. See [parameters and updates](modelling-workflows.md).

#### Method: `set_parameter`

```text
set_parameter(parameter_or_name, value)
```

Accept a registered parameter or its name and a value satisfying that parameter's numeric/bound rules. Return the normalized `float` value. All coefficient, RHS, and bound bindings are updated together; if an update fails, the previous parameter value and bound model fields are restored. Unknown names raise `ModelValidationError`. Calling `Parameter.set()` directly changes only the parameter object; use this method to propagate model bindings.

#### Method: `add_constraint`

```text
add_constraint(constraint, penalty_weight=None, label=None)
```

Accept a relational constraint (`Equal`, `LessEqual`, `GreaterEqual`), `Indicator`, `LogicalConstraint`, `CustomPenalty`, `SOS1`, or `SOS2`; append a record and return the same constraint object. `label` is `None` or a nonempty single-line string. Its effective value must be unique; absent labels use `c0`, `c1`, and so on, except that a `CustomPenalty` supplies its own label when present.

`penalty_weight` is `None` for automatic selection or a finite positive non-Boolean real number used during polynomial compilation. Indicators and SOS sets reject explicit penalty weights. Duplicate SOS names also fail. Invalid arguments raise `ConstraintValidationError` before insertion. Addition does not perform complete target validation; call the relevant compiler/exporter to verify representability.

#### Method: `add_piecewise_linear`

```text
add_piecewise_linear(source, output, breakpoints, values, *, name=None)
```

Accept distinct `Variable` objects `source` and `output` with distinct names, equal-length finite numeric `breakpoints` and `values` iterables, and an optional nonempty single-line `name`. At least two strictly increasing breakpoints are required. Return a `PiecewiseLinear` descriptor after adding continuous interpolation weights, three linear linking equalities, and an SOS2 set. Invalid inputs leave the model unchanged. See [piecewise-linear modelling](piecewise-linear.md) for the domain and exact interpolation contract.

#### Method: `variables`

```text
variables()
```

Return `list[Variable]` in first-encounter order: explicitly registered variables, objective factors, then constraint factors. Repeated object identities appear once; distinct source objects sharing a name raise `ModelValidationError`. An objective is not required. The returned list contains the original variable objects, including generated modelling variables when present.

#### Method: `validate`

```text
validate(target=None)
```

Require an objective, validate source structure, and return `str`. `target` is `None` or a case-insensitive class name: `LP`, `MILP`, `MIP`, `QP`, `QUBO`, `HUBO`, or `ISING`. Whitespace is not stripped. Unknown targets raise `ModelValidationError`; an unset objective raises `EmptyModelError`.

The three linear targets share structural checks and return the actual `LP`, `MILP`, or `MIP` classification; `validate("LP")` alone does not require continuous variables. `QP` requires a quadratic objective, continuous variables, and linear relational constraints; curvature is checked by `to_qp_matrix()`. QUBO/HUBO checks reject spins, indicators, and SOS sets; QUBO also rejects an objective above degree two. Native `ISING` requires spin-only, unconstrained, at-most-quadratic interactions after spin-power reduction.

With no target, classification considers spin models first, then higher-order binary models and unconstrained quadratic binary models, then linear models, continuous QP, and other supported quadratic binary models. Structural validation does not establish successful domain encoding, sufficient penalties, feasible solutions, or curvature; use the complete target operation for those checks.

#### Method: `compile`

```text
compile(target=None, **options)
```

Validate and package a problem with an independent source snapshot; no solving or authentication occurs. `target=None` uses the detected class and normalizes `MIP` to `MILP`. Explicit targets are case-insensitive `LP`, `MILP`, `QP`, `QUBO`, `HUBO`, or `ISING`; explicit `MIP` is not accepted.

| Target | Return type | Options |
| --- | --- | --- |
| `LP`, `MILP` | `MILPProblem` | Only `config`, following `MILPProblem.from_model()` |
| `QP` | Independently copied `Model` | None; matrix export and curvature validation must succeed |
| `QUBO` | `QUBOProblem` | Domain-encoding and penalty options accepted by `QUBOProblem.from_model()` |
| `HUBO` | `HUBOProblem` | Options accepted by `HUBOProblem.from_model()` |
| `ISING` | `IsingProblem` | Options accepted by `IsingProblem.from_model()` |

Explicit `LP` compilation rejects retained discrete variables. Unsupported options raise `TypeError`; unsupported targets raise `ModelValidationError`. Complete encoding, numerical, and packaging checks apply in addition to structural validation. See the problem-class guides for the exact constructor options and failure types.

#### Method: `estimate_penalty_weight`

```text
estimate_penalty_weight(constraint, **compile_options)
```

Return the compiler's automatic hard-constraint weight as `float` without changing model state. Set the objective before calling this method. Supply a constraint object, effective label, or zero-based non-Boolean integer index; an unattached object is added only to the estimation snapshot. Unknown labels or indices raise `ModelValidationError`. A `CustomPenalty`, whether supplied directly or selected by label/index, raises `ConstraintValidationError` because it has no automatic hard-constraint weight. Redundant constraints return `0.0`. Encoding options and compilation failures follow `to_qubo_triplets()`, or `to_hubo_dict()` when the source objective has degree above two.

#### Method: `to_qubo_triplets`

```text
to_qubo_triplets(*, matrix_format='symmetric', continuous_bits=None, continuous_precision=None, max_bits=20, strict_penalties=False)
```

Return `(rows, columns, coefficients, offset)`: one-dimensional NumPy `int64`, `int64`, and `float64` arrays of equal length, plus a `float` offset. Indices address the latest `qubo_variable_names`, including generated encoding variables. `"upper"` stores direct polynomial coefficients; `"symmetric"` splits off-diagonal coefficients across mirrored entries. The represented energy is normalized to minimization, including the offset for source MAX models.

`continuous_bits`, `continuous_precision`, and `max_bits` control finite domain encoding; neither continuous resolution option is inferred. `max_bits=20` limits each continuous variable's encoding. `strict_penalties` is a Boolean. See [QUBO encoding options](qubo-modeller.md) for exact resolution and domain rules. Compilation failures restore the prior compiled state; no partial decoding map is published.

`strict_penalties=False` is the default. An explicit hard-constraint weight below the computed safe threshold emits `UserWarning`: `Selected penalty may be insufficient. Recommended penalty weight: {x}.` Here `x` is that threshold. `strict_penalties=True` raises `ConstraintValidationError` instead. Omitted weights still use automatic safe selection.

#### Method: `to_qubo_dict`

```text
to_qubo_dict(*, matrix_format='symmetric', **compile_options)
```

Return `(dict[tuple[int, int], float], float)` containing QUBO coefficients and offset. Matrix conventions, minimization normalization, encoding options, penalties, and compiled-state behaviour match `to_qubo_triplets()`.

#### Method: `to_ising_dict`

```text
to_ising_dict(**compile_options)
```

Return a dictionary with `problem_type="ISING"`, `num_variables: int`, `sense: "MIN" | "MAX"`, `offset: float`, `variable_names: list[str]`, `linear: dict[int, float]`, and `quadratic: dict[tuple[int, int], float]`. Coefficients and offset retain the source objective sense. Native spin models accept no compile options. Other supported sources compile through upper QUBO; pass its encoding/penalty options, excluding `matrix_format`. Mixed spin/non-spin sources fail. See [Ising conventions](ising-modeller.md).

#### Method: `to_hubo_dict`

```text
to_hubo_dict(*, continuous_bits=None, continuous_precision=None, max_bits=20, strict_penalties=False)
```

Return `dict[tuple[str, ...], float]` with sorted compiled binary labels in each monomial; the empty tuple holds a nonzero constant when present. Higher source degrees are retained. Energy, including the constant, is normalized to minimization. Encoding/penalty options follow `to_qubo_triplets()` except that no matrix format is needed. The latest compiled label order is in `hubo_variable_names`. See [HUBO compilation](hubo-modeller.md).

#### Method: `transformations`

```text
transformations()
```

Return a deeply copied `list[dict]`. Modelling records are retained across compilation; derived records describe the latest successful compilation rather than accumulating a history of every compile. Editing the returned records does not change the model. See [transformation records](transformations.md).

#### Method: `to_ommx`

```text
to_ommx()
```

Return `ommx.v1.Instance` using the same subset, dependency requirements, and metadata behaviour as module-level `to_ommx(model)` below.

#### Method: `from_ommx`

```text
from_ommx(instance)
```

Class method returning `Model` from `ommx.v1.Instance`; equivalent to module-level `from_ommx(instance)` below.

#### Method: `qubo_index`

```text
qubo_index(variable_or_name)
```

Accept a variable object or its name and return its integer index in the latest successful QUBO compilation. An absent name raises `KeyError`; `Variable.index` is not a substitute for this compact index.

#### Method: `decode_qubo_sample`

```text
decode_qubo_sample(sample)
```

Return `dict[str, int | float]` in source variable coordinates using the latest QUBO label order and encoding map; compiler auxiliary variables are omitted. `sample` is an iterable in that order or a dictionary with every compiled bit addressed by label or integer index. Values must be binary; Boolean bits are accepted. Missing dictionary entries raise `KeyError`; wrong lengths, nonbinary values, or conflicting label/index entries raise `ValueError`. Decode against the compilation that produced the sample; recompile after source edits.

#### Method: `decode_hubo_sample`

```text
decode_hubo_sample(sample)
```

Return source coordinates using the latest HUBO compilation. Inputs, return types, auxiliary omission, and errors match `decode_qubo_sample()`, with `hubo_variable_names` defining the input order.

#### Method: `evaluate_objective`

```text
evaluate_objective(values)
```

Return the source objective's `float` value, including its constant, without changing its MIN/MAX sense or checking feasibility. `values` maps variable objects or their names to finite real values and must cover the objective's referenced variables; extra entries are ignored. An unset objective raises `EmptyModelError`; invalid assignments and unrepresentable arithmetic follow `Expression.evaluate()`.

#### Method: `constraint_violations`

```text
constraint_violations(values, *, tolerance=1e-06)
```

Return `dict[str, float]` covering source variable domains and hard constraints. `values` maps source variables or their names to values; `tolerance` must be a finite nonnegative non-Boolean real number. Domain entries appear only when a violation exceeds tolerance, under `variable:<name>`. Every evaluated hard constraint has an entry, including `0.0` when satisfied within tolerance. Constraint keys use effective labels, with a `constraint:` prefix when needed to avoid collisions. `CustomPenalty` contributes no hard-feasibility condition.

Missing, conflicting, or unevaluable source values produce infinite domain violations, and hard-constraint evaluation is skipped when the assignment cannot be evaluated. Finite out-of-domain values still permit constraint checks. Piecewise interpolation weights are derived when the source coordinates suffice. Unrepresentable relation arithmetic raises `NumericalStabilityError`. See [solution verification](milp-modeller.md#evaluate-and-verify-a-solution) for row scaling and domain rules.

#### Method: `is_feasible`

```text
is_feasible(values, *, tolerance=1e-06)
```

Return `bool`, true exactly when every violation returned by `constraint_violations()` is zero. Uses the same assignment, tolerance, domain, and error rules; it checks variable domains as well as hard constraints.

#### Method: `to_lp_matrix`

```text
to_lp_matrix(for_milp=True, *, sparse_matrix=True)
```

Return the [linear matrix dictionary](milp-modeller.md#matrix-representation) after linear structural and reformulation checks. Both options require actual Python `bool` values. `for_milp=True` reformulates SOS sets; `False` retains native SOS records. Domain and indicator reformulations apply in either mode. `sparse_matrix=True` returns a SciPy CSR constraint matrix; `False` returns a NumPy array. This operation does not solve the model.

#### Method: `to_qp_matrix`

```text
to_qp_matrix(*, sparse_matrix=True)
```

Return a dictionary extending the [linear matrix contract](milp-modeller.md#matrix-representation) with a Hessian and [QP metadata](qp-modeller.md#matrix-convention). `sparse_matrix` requires a Python `bool`: `True` returns CSR constraints and a CSC Hessian; `False` returns dense arrays. Requires a continuous quadratic objective with linear relational constraints. Wrong curvature raises `ModelValidationError`; uncertain numerical curvature raises `NumericalStabilityError`. No solve or authentication occurs.

#### Method: `to_lp_string`

```text
to_lp_string(for_milp=True)
```

Return newline-terminated CPLEX LP text as `str` after linear export and portable-name validation. The Boolean `for_milp` option has the same SOS meaning as `to_lp_matrix()`. See [file export](milp-modeller.md#lp-mps-and-gzip-export) for naming, domain reformulations, and constant-only models.

#### Method: `to_lp_file`

```text
to_lp_file(filepath, for_milp=True)
```

Accept a filesystem path (`str` or path-like), write UTF-8 LP text with LF newlines, and return `str(path)`. The filename must end in `.lp` or `.lp.gz`, case-insensitively; otherwise raise `ValueError`. Gzip compression follows the suffix. Content is validated before opening the destination. Parent directories are not created; filesystem errors propagate.

#### Method: `to_mps_string`

```text
to_mps_string(for_milp=True)
```

Return newline-terminated free-format MPS text as `str`. Linear validation, naming, and the Boolean `for_milp` option follow `to_lp_string()`.

#### Method: `to_mps_file`

```text
to_mps_file(filepath, for_milp=True)
```

Write free-format MPS and return `str(path)`. Path types, UTF-8/LF encoding, compression, validation order, and filesystem behaviour match `to_lp_file()`, with required suffix `.mps` or `.mps.gz`.

#### Method: `objective_as_string`

```text
objective_as_string()
```

Return `str` containing the source objective and direction. An unset objective raises `ObjectiveError`.

#### Method: `summary`

```text
summary(*, max_constraints=10)
```

Return a deterministic human-readable `str`. `max_constraints` is a nonnegative non-Boolean integer controlling how many constraint rows are shown; invalid values raise `ValueError`. A summary can describe an unsupported or incomplete model and does not replace target validation.

#### Method: `pretty_print`

```text
pretty_print(*, max_constraints=10)
```

Print `summary(max_constraints=max_constraints)` and return the same string. The option has the same validation rules as `summary()`.

#### Method: `print_objective`

```text
print_objective()
```

Print and return `objective_as_string()`. An unset objective raises `ObjectiveError`.

#### Property: `sense`

```python
sense
```

Get or assign the source direction as `"MIN"` or `"MAX"`. Strings are uppercased; invalid values raise `ObjectiveError`. Assignment alone does not negate or otherwise modify the objective expression.

## Function: `to_ommx`

```text
to_ommx(model: 'Model')
```

Accept `Model` and return `ommx.v1.Instance`. Requires an objective and the optional `qpiai-opt[ommx]` dependency. Preserves a polynomial objective and constant, source MIN/MAX sense, linear relations, and binary/integer/continuous/semi-continuous/semi-integer domains. Spin, fractional, mixed, logical, indicator, SOS, and custom-penalty structures are rejected.

Variable IDs are consecutive integers in `model.variables()` order; constraint IDs follow insertion order. Successful export appends a transformation record with `stage="interoperability"`, `target="OMMX"`, and `variable_ids: dict[str, int]`. Wrong input types raise `TypeError`, missing dependencies raise `ImportError`, an unset objective raises `EmptyModelError`, and unsupported structures or coefficient overflow raise `ModelValidationError`. See the [OMMX bridge](interoperability.md#ommx-bridge).

## Function: `from_ommx`

```text
from_ommx(instance) -> 'Model'
```

Accept `ommx.v1.Instance` and return an independently constructed `Model`. The model name uses `instance.title`, or `"OMMX model"` when absent. Import all declared variables, polynomial objective and sense, and linear equality/less-than-or-equal-to-zero constraints. Integer, continuous, semi-continuous, and semi-integer bounds are preserved. Binary variables are reconstructed with domain `{0, 1}`; express fixed binary values as explicit linear equalities before import. Missing variable names become `x[id]`; missing constraint names become `c{id}`.

Variable IDs and names must be unique, including generated names; OMMX subscripts do not distinguish duplicate names. Every polynomial factor must reference a declared ID and have a finite real coefficient. Unsupported kinds, objective senses, constraint relations, nonlinear constraints, duplicate variable IDs/names, or unrepresentable aggregation raise `ModelValidationError`. Variable and constraint constructor validation also applies, including unique effective constraint labels. Wrong input types raise `TypeError`; a missing optional dependency raises `ImportError`. The resulting transformation record contains `source="OMMX"` and the source `variable_ids` map. See the [OMMX bridge](interoperability.md#ommx-bridge) for unsupported OMMX features.

## Class: `Expression`

```text
Expression(terms=None, constant=0.0)
```

A polynomial expression over SDK variables.

`terms` accepts an iterable of `Term` objects or `None`; `constant` is a finite non-Boolean real number normalized to `float`. Construction canonicalizes repeated binary/spin factors, folds constant terms into the constant, combines equivalent symbolic terms, and removes zero totals. Unsupported factors and non-finite or unrepresentable arithmetic raise `InvalidExpressionError`.

### Arithmetic and comparison operators

These operators apply to `Expression` and scalar `Variable` objects:

| Operation | Inputs and result |
| --- | --- |
| `a + b`, `a - b`, `a * b` | Accept variables, expressions, and finite non-Boolean real scalars; return an `Expression`. Reflected scalar operations are supported. Multiplication expands the polynomial subject to the [construction guards](performance.md#construction-and-compilation-boundaries). |
| `a / scalar` | Return an `Expression` divided by a finite nonzero real scalar. Zero raises `ZeroDivisionError`; invalid divisors and division by a symbolic variable or expression raise `InvalidExpressionError`. |
| `-a`, `+a` | Return an `Expression`; unary plus retains an existing expression unchanged. |
| `a ** exponent` | Require a nonnegative non-Boolean integral exponent. Zero returns the constant-one expression; one retains an existing expression. Binary powers reduce by idempotence and spin powers by parity. Other polynomial powers expand subject to construction guards. Invalid exponents or symbolic exponents raise `InvalidExpressionError`. |
| `a <= b`, `a >= b`, `a == b` | Return `LessEqual`, `GreaterEqual`, or `Equal`, respectively. Both sides must be linear and satisfy the relational input contract below. |

Unsupported arithmetic operand types can raise Python `TypeError` after operator dispatch. Relations have no Boolean truth value; add separate constraints instead of chained comparisons. Use `is` for variable object identity.

### Methods and properties

#### Method: `from_terms`

```text
from_terms(terms, constant=0.0)
```

Class method equivalent to the constructor; returns `Expression`. `terms` and `constant` have the same input contracts.

#### Method: `to_binary_quadratic_arrays`

```text
to_binary_quadratic_arrays()
```

Return `(coefficients, indices, constant)` with a one-dimensional NumPy `float64` array, an `(n, 2)` NumPy `int64` array, and a `float`. Each row represents a binary term using opaque `Variable.index` identities; `-1` marks an absent factor. The arrays are detached copies. Stored terms can repeat or cancel algebraically; these arrays are not canonical QUBO indices or a guarantee of unique monomials. An explicitly constant-only expression has empty arrays. Higher-order or nonbinary expressions raise `InvalidExpressionError`; use `to_terms()` and `.constant` instead.

#### Method: `to_terms`

```text
to_terms()
```

Return `list[Term]`, a detached list of stored symbolic terms. Terms may repeat in expressions built through array-based arithmetic; evaluate or compile the full expression rather than treating the list as a unique-monomial dictionary.

#### Method: `degree`

```text
degree(raise_on_higher_order=False)
```

Return the integer degree of the retained terms, with zero for a constant-only expression. `raise_on_higher_order` must be a strict Boolean; `True` raises `NonlinearExpressionError` above degree two.

#### Method: `evaluate`

```text
evaluate(values)
```

Return a `float` for a mapping keyed by variable object or variable name. Object keys take precedence when both are supplied. Every referenced variable must be present; extra keys are permitted. Finite real assignment values are required, with Boolean values accepted as 0/1. This method performs polynomial arithmetic, not domain or constraint validation; use `Model.is_feasible` or `feasibility` for that. A missing variable raises `KeyError`; malformed values or unrepresentable arithmetic raise `InvalidExpressionError`.

#### Method: `to_readable_string`

```text
to_readable_string()
```

Return `str` containing a deterministic algebraic representation of the polynomial.

#### Property: `terms`

```python
terms
```

Read as `list[Term]`, equivalent to `to_terms()`. Assignment requires a `list` of `Term` objects and replaces the stored terms after validation; new constant terms are folded into the current constant. A failed assignment leaves the expression unchanged.

#### Property: `constant`

```python
constant
```

Read or replace the finite `float` constant. Assignment rejects Boolean, non-real, non-finite, and unrepresentable values with `InvalidExpressionError`.

## Class: `Term`

```text
Term(coefficient, variables)
```

A coefficient multiplied by zero or more decision variables.

`coefficient` is a finite non-Boolean real number normalized to `float`. `variables` is an iterable containing only `Variable` instances. Binary factors collapse using $x^2=x$; spin factors cancel in pairs using $s^2=1$. At most 100,000 canonical factors can remain. The read-only `coefficient: float` and `variables: tuple[Variable, ...]` properties expose the result; an empty factor tuple represents a constant. Invalid coefficients or factors raise `InvalidExpressionError`.

### Methods and properties

#### Property: `coefficient`

```python
coefficient
```

Return the finite scalar coefficient.

#### Property: `variables`

```python
variables
```

Return the ordered variable factors.

## Class: `Parameter`

```text
Parameter(name: 'str', value: 'float') -> None
```

A named finite value that can drive explicit model-update bindings.

`name` must be a non-empty string without NUL or line breaks. `value` must be a finite non-Boolean real number; construction and `set` normalize it to `float`. `set` updates the parameter alone. Use `Model.set_parameter` to apply its registered bindings atomically. Invalid names or values raise `ValueError`. `as_dict()` returns `{"name": name, "value": value}`.

### Methods and properties

#### Method: `set`

```text
set(value: 'float') -> 'float'
```

Validate, store, and return a new parameter value.

#### Method: `as_dict`

```text
as_dict() -> 'dict[str, float | str]'
```

Return the parameter as a JSON-compatible mapping.

## Class: `PiecewiseLinear`

```text
PiecewiseLinear(name: 'str', source: 'Variable', output: 'Variable', breakpoints: 'tuple[float, ...]', values: 'tuple[float, ...]', weights: 'tuple[Variable, ...]') -> None
```

Variables and coordinates of one exact SOS2 interpolation formulation.

Normally obtain this frozen descriptor from `Model.add_piecewise_linear`; that method validates the coordinates and adds the required equations. Direct descriptor construction only stores its fields. `source` and `output` are the original variables, `breakpoints` and `values` are the interpolation coordinates, and `weights` are the generated continuous convex-combination variables. See [piecewise modelling](piecewise-linear.md).

## Class: `IndexedVariables`

```text
IndexedVariables(variable_type: 'type[Variable]', name: 'str', *index_sets: 'Iterable[Any]', **variable_options: 'Any') -> 'None'
```

Deterministic one- or multidimensional variables keyed by index tuples.

`variable_type` must be a `Variable` subclass. `name` is a non-empty string without NUL, line breaks, commas, or brackets. Supply at least one finite iterable axis; axis items are unique simple strings or finite real values, including Boolean indices. Options are passed by keyword to each scalar constructor. Axes are consumed once and stored in `index_sets: tuple[tuple, ...]`; `shape: tuple[int, ...]` stores axis lengths. An empty axis creates no variables. Invalid axes, duplicate generated names, and invalid scalar options fail construction. See [indexing and selection](modelling-workflows.md#multidimensional-variables).

`indexed[key]` returns its scalar variable. One-dimensional keys may be scalar or a one-element tuple; higher-dimensional keys must be tuples of the exact dimension. Missing, unhashable, or dimensionally invalid keys raise `KeyError`. `len(indexed) -> int` returns the total variable count, and `iter(indexed)` yields public keys in Cartesian-product order. The container implements the read-only `Mapping` interface; membership tests use its key lookup.

### Methods and properties

#### Method: `values`

```text
values()
```

Return a `dict_values` view of scalar `Variable` objects in Cartesian-product order. An empty collection returns an empty view.

#### Method: `items`

```text
items()
```

Return an iterator of `(key, Variable)` pairs in Cartesian-product order. A key is the scalar axis value for a one-dimensional collection and an index tuple for higher dimensions.

#### Method: `keys`

```text
keys()
```

Return a mapping view over public indices in deterministic Cartesian-product order.

#### Method: `get`

```text
get(key, default=None)
```

Return the variable for `key`, or `default` when the key is absent. One-dimensional keys may be scalar; higher-dimensional keys must be tuples.

#### Method: `select`

```text
select(*pattern: 'Any') -> 'tuple[Variable, ...]'
```

Return `tuple[Variable, ...]` matching one selector per axis, in Cartesian-product order. `None` or `"*"` matches every value on that axis. An unmatched pattern returns `()`; the wrong selector count raises `ValueError`.

#### Method: `sum`

```text
sum(*pattern: 'Any')
```

Return `Expression`: the symbolic sum of all variables when no pattern is supplied, or the variables returned by `select(*pattern)`. An empty selection returns the constant-zero expression. Pattern errors follow `select()`.

## Class: `Binary`

```text
Binary(name)
```

Binary decision variable with domain ``{0, 1}``.

## Class: `Spin`

```text
Spin(name)
```

An Ising spin decision variable with domain ``{-1, +1}``.

## Class: `Integer`

```text
Integer(name, lower_bound=0, upper_bound=None)
```

Integer decision variable over declared bounds.

## Class: `Continuous`

```text
Continuous(name, lower_bound=0.0, upper_bound=inf)
```

Continuous decision variable over a real interval.

## Class: `SemiContinuous`

```text
SemiContinuous(name, lower_bound, upper_bound)
```

Decision variable equal to zero or within a positive interval.

## Class: `SemiInteger`

```text
SemiInteger(name, lower_bound, upper_bound)
```

Integer decision variable equal to zero or within a positive interval.

## Class: `Fraction`

```text
Fraction(name, lower_bound, upper_bound, precision_decimals=2)
```

Fixed-point decimal decision variable with explicit precision.

## Class: `Mixed`

```text
Mixed(name, valid_discrete_values, continuous_range)
```

Decision variable over the union of discrete values and a real interval.

## Function: `create_binary_array`

```text
create_binary_array(prefix, size)
```

Return ``size`` binary variables named from ``prefix``.

## Function: `create_spin_array`

```text
create_spin_array(prefix, size)
```

Return ``size`` spin variables named from ``prefix``.

## Class: `Variable`

```text
Variable(name, var_type)
```

Base class for scalar decision variables. `name` uses the common validation below; `var_type` is stored as supplied and does not create or validate a domain. Use the concrete constructors for supported domains and their bounds.

### Methods and properties

#### Property: `var_type`

```python
var_type
```

Return the stored variable-domain identifier.

#### Property: `index`

```python
index
```

Return the process-local expression identity token for a `Binary`; the other public variable constructors return `None`. Treat a non-`None` value as opaque and process-local. This is not a QUBO coordinate; use `Model.qubo_index()` after compilation for QUBO coordinates.

Every variable exposes `name`, `var_type`, and `index`. `Integer`, `Continuous`, `SemiContinuous`, `SemiInteger`, and `Fraction` also expose normalized `lower_bound` and `upper_bound`. `Fraction.precision_decimals` is the validated decimal-grid precision. `Mixed.valid_discrete_values` and `Mixed.continuous_range` are normalized tuples.

All constructors require `name: str` to be non-empty and contain no NUL or line breaks. Prefer the concrete domain constructors over constructing the base `Variable` directly. Their additional input contracts are:

| Class | Bounds or values | `var_type` |
| --- | --- | --- |
| `Binary` | No bound arguments; domain `{0, 1}`. | `"BINARY"` |
| `Spin` | No bound arguments; domain `{-1, +1}`. | `"SPIN"` |
| `Integer` | Finite bounds are integral real values in `[-(2**53-1), 2**53-1]`; lower `-inf` and upper `inf` are allowed. `upper_bound=None` becomes `inf`. | `"INTEGER"` |
| `Continuous` | Real non-Boolean bounds; lower `-inf` and upper `inf` are allowed. NaN and `None` are rejected. | `"CONTINUOUS"` |
| `SemiContinuous` | Finite real bounds with `0 < lower_bound <= upper_bound`; zero remains a separate allowed value. | `"SEMI_CONTINUOUS"` |
| `SemiInteger` | Integral bounds in `[1, 2**53-1]` with lower no greater than upper; zero remains allowed. | `"SEMI_INTEGER"` |
| `Fraction` | Finite bounds on the declared decimal grid; `precision_decimals` is an integer from 0 to 15. Scaled bound magnitude cannot exceed `2**53-1`. | `"FRACTIONAL"` |
| `Mixed` | A non-empty iterable of distinct finite real `valid_discrete_values` and a finite ordered two-element list or tuple `continuous_range`. | `"MIXED"` |

Lower bounds cannot exceed upper bounds. Invalid names raise `InvalidExpressionError`; invalid domains raise `BoundsError`. `create_binary_array(prefix, size) -> list[Binary]` and `create_spin_array(prefix, size) -> list[Spin]` require a non-empty string prefix and a non-negative non-Boolean integral size. They return names `prefix_0`, `prefix_1`, and so on; size zero returns an empty list. Generated names obey the scalar name rules.

## Function: `quicksum`

```text
quicksum(items)
```

Return `Expression` from an iterable of `Variable`, `Expression`, or finite non-Boolean real values. An empty iterable returns the constant-zero expression. Invalid items or unrepresentable aggregation raise `InvalidExpressionError`. Prefer this operation over repeated addition for a large expression.

## Function: `dot`

```text
dot(coefficients, variables)
```

Return `Expression` from equal-length iterables of finite non-Boolean real `coefficients` and `Variable` instances. Both iterables are consumed once; an empty pair returns the constant-zero expression. Length mismatch, malformed input, or unrepresentable arithmetic raises `InvalidExpressionError`.

## Constant: `IR_VERSION`

```python
IR_VERSION = 'qpiai-opt-ir/1.0'
```

## Function: `to_ir`

```text
to_ir(value: 'Any') -> 'dict[str, Any]'
```

Return an IR dictionary for `Model`, `QUBOProblem`, `HUBOProblem`, `IsingProblem`, `MILPProblem`, or `QubitHamiltonian`. A linear problem container emits its mathematical model, excluding execution configuration. Other objects raise `TypeError`. Caller-supplied transformation metadata must use JSON-compatible values. The [IR contract](model-ir.md) defines every kind, field, numeric rule, and retained metadata.

## Function: `from_ir`

```text
from_ir(document: 'Mapping[str, Any]') -> 'Any'
```

Accept a `Mapping` with `ir_version="qpiai-opt-ir/1.0"` and return the native object selected by its `kind`. Non-mappings raise `TypeError`; unsupported versions, kinds, fields, references, or numeric values raise validation errors. A `kind="model"` document returns `Model`, including when produced from `MILPProblem`. See the [IR function and schema contracts](model-ir.md#public-functions).

## Function: `dumps`

```text
dumps(value: 'Any', *, indent: 'int | None' = None) -> 'str'
```

Return the canonical IR as a key-sorted JSON `str`. `value` accepts the objects supported by `to_ir()`. Keyword-only `indent=None` emits a single line; an integer controls indentation. Serialization rejects NaN and implicit infinities; unbounded limits use the IR's explicit string sentinels. Conversion and JSON serialization errors propagate.

## Function: `loads`

```text
loads(payload: 'str | bytes | bytearray') -> 'Any'
```

Parse JSON from `str`, `bytes`, or `bytearray`, then apply `from_ir()`. Return its reconstructed model, polynomial problem, or Hamiltonian. Invalid JSON syntax raises `json.JSONDecodeError`; duplicate object keys, non-standard NaN/infinity tokens, and invalid IR raise `ValueError` or the relevant model validation error.

## Function: `dump`

```text
dump(value: 'Any', path: 'str | Path', *, indent: 'int' = 2) -> 'Path'
```

Write `dumps(value, indent=indent)` followed by a newline as UTF-8 to `path: str | Path`, replacing existing file contents, and return the destination `Path`. `indent` is keyword-only and defaults to two spaces. Parent directories must exist. Conversion, serialization, and filesystem errors propagate.

## Function: `load`

```text
load(path: 'str | Path') -> 'Any'
```

Read UTF-8 text from `path: str | Path` and pass it to `loads()`, returning the reconstructed object. Filesystem, JSON parsing, and IR validation errors propagate.

## Relational constraints

Comparisons between expressions and variables construct `Equal`, `LessEqual`, or `GreaterEqual`; they do not return a Python truth value. Explicit constructors are available from `qpiai_opt.opt_modeller.constraints`:

```text
Constraint(lhs, rhs)
Equal(lhs, rhs)
LessEqual(lhs, rhs)
GreaterEqual(lhs, rhs)
```

`lhs` and `rhs` accept `Variable`, `Expression`, or finite non-Boolean real constants. Both sides must be linear. Invalid operand types or nonlinear relations raise `ConstraintValidationError`; invalid expression arithmetic follows `Expression` validation. Stored `lhs` and `rhs` are expressions, and an input expression is retained by reference. Boolean conversion raises `TypeError`, including Python chained comparisons; add each relation separately.

`Constraint` is a base interface. Its `to_penalty()` raises `NotImplementedError`. Concrete `to_penalty()` methods return an `Expression`:

| Relation | Penalty |
| --- | --- |
| `Equal` | Squared residual `(lhs - rhs)**2`. |
| `LessEqual` | Squared residual with nonnegative binary slack for `lhs - rhs <= 0`. |
| `GreaterEqual` | Squared residual with nonnegative binary slack for `rhs - lhs <= 0`. |

Direct inequality penalty conversion requires binary-expanded factors. A redundant inequality returns zero; an inequality infeasible over its bounds raises `ConstraintValidationError`. Unexpanded domains or an unrepresentable slack lattice raise `EncodingError`. These methods cache the resulting expression. Prefer model compilation for source domains, automatic constraint encoding, penalty weights, and sample decoding; changing a stored RHS should use `Model.set_constraint_rhs()` to invalidate its cached penalty.

## Class: `SOS1`

```text
SOS1(name, variables, weights=None)
```

At most one member may be nonzero. `name` is a nonempty single-line string; `variables` is a nonempty iterable of `Variable` objects with unique names. `weights=None` assigns `1.0, 2.0, ...`; an explicit iterable must contain one unique finite non-Boolean real weight per member. Invalid inputs raise `ConstraintValidationError`.

Public attributes are `name: str`, `variables: tuple[Variable, ...]`, `weights: tuple[float, ...]`, and `sos_type="SOS1"`. `to_penalty()` raises `ConstraintValidationError`; use linear export or solving. Boolean conversion raises `TypeError`.

## Class: `SOS2`

```text
SOS2(name, variables, weights=None)
```

At most two members may be nonzero, and two active members must be adjacent after ordering by weight. Constructor inputs, stored tuples, validation errors, and `to_penalty()`/Boolean behaviour match `SOS1`; `sos_type="SOS2"`. Input weights need not be supplied in sorted order. See [SOS export](milp-modeller.md#sos1-and-sos2).

## Class: `Indicator`

```text
Indicator(binary, active_value, constraint)
```

Enforce `constraint` when `binary == active_value`. `binary` must be `Binary`; `active_value` must be integral `0` or `1` (Boolean values are accepted and normalized to `int`); `constraint` must be `Equal`, `LessEqual`, or `GreaterEqual`. Invalid inputs raise `ConstraintValidationError`. Public attributes retain the trigger, normalized active value, and guarded relation. Boolean conversion raises `TypeError`. Linear export derives bounded big-M rows; QUBO/HUBO compilation rejects indicators.

### Methods and properties

#### Method: `variables`

```text
variables()
```

Return `tuple[Variable, ...]`: trigger first, then factors encountered in the guarded relation, with object identities deduplicated.

`LogicalConstraint` is also exported by `qpiai_opt.opt_modeller.constraints` as the base interface for the following logical constraints. Its `variables()` and `to_penalty()` raise `NotImplementedError`; use a concrete relation. Its Boolean conversion raises `TypeError`.

## Class: `AND`

```text
AND(x, y, z)
```

Enforce `z = x AND y`. All arguments must be distinct `Binary` objects; otherwise raise `ConstraintValidationError`. Public attributes `x`, `y`, and `z` retain those objects. The same input and Boolean-conversion rules apply to `OR`, `XOR`, and `NOT`: a logical constraint has no truth value and raises `TypeError` when used as a Python condition.

### Methods and properties

#### Method: `variables`

```text
variables()
```

Return `(x, y, z)` as a tuple of the original `Binary` objects.

#### Method: `to_penalty`

```text
to_penalty()
```

Return `Expression` for `x*y - 2*x*z - 2*y*z + 3*z`. It is nonnegative on binary inputs and zero exactly when the relation holds.

## Class: `OR`

```text
OR(x, y, z)
```

Enforce `z = x OR y`, retaining distinct `Binary` arguments as public attributes `x`, `y`, and `z`. Validation rules match `AND`.

### Methods and properties

#### Method: `variables`

```text
variables()
```

Return `(x, y, z)` as a tuple of the original `Binary` objects.

#### Method: `to_penalty`

```text
to_penalty()
```

Return `Expression` for `x*y - 2*x*z - 2*y*z + x + y + z`. It is nonnegative on binary inputs and zero exactly when the relation holds.

## Class: `XOR`

```text
XOR(x, y, z)
```

Enforce `z = x XOR y` using a binary product auxiliary. Inputs follow `AND`; public attributes are `x`, `y`, `z`, and `auxiliary: Binary`. Auxiliary names are generated and are not stable source identifiers.

### Methods and properties

#### Method: `variables`

```text
variables()
```

Return `(x, y, z, auxiliary)` as `tuple[Binary, Binary, Binary, Binary]`.

#### Method: `to_penalty`

```text
to_penalty()
```

Return `Expression` for `(x + y - z - 2*auxiliary)**2`. Minimizing over the binary auxiliary gives zero exactly for a valid XOR relation. An arbitrary auxiliary value need not give zero for an otherwise valid source assignment.

## Class: `NOT`

```text
NOT(x, y)
```

Enforce `y = NOT x`. Both arguments must be distinct `Binary` objects; invalid inputs raise `ConstraintValidationError`. Public `x` and `y` attributes retain them.

### Methods and properties

#### Method: `variables`

```text
variables()
```

Return `(x, y)` as a tuple of the original `Binary` objects.

#### Method: `to_penalty`

```text
to_penalty()
```

Return `Expression` for `(x + y - 1)**2`, nonnegative on binary inputs and zero exactly when the relation holds.

## Class: `CustomPenalty`

```text
CustomPenalty(expression, label=None)
```

Accept an `Expression` or `Variable` and an optional nonempty single-line string `label`. A variable is converted to an expression; an expression is retained by reference. Numeric constants alone are not accepted. Invalid inputs raise `ConstraintValidationError`. Public attributes are `expression: Expression` and `label: str | None`.

This is a user-supplied objective contribution, not a hard-feasibility condition. The SDK does not infer its desired zero set or prove that it is nonnegative. Model compilation applies its weight; hard-constraint feasibility checks omit it.

### Methods and properties

#### Method: `to_penalty`

```text
to_penalty()
```

Return the same stored `Expression`, without scaling or copying it.

## Function: `encode_integer_binary`

```text
encode_integer_binary(integer_var)
```

Accept an `Integer` with finite integral bounds and return an `Expression` representing its entire closed integer interval. The lower bound is the offset; bounded binary weights cover every allowed integer and no value outside the interval. A fixed variable uses no bits. Invalid domains or excessive exact-integer resolution raise `EncodingError`.

The returned expression has `.encoding: dict` with `source` (the original variable), `kind="integer"`, `binaries: tuple[Binary, ...]`, `weights: tuple[float, ...]`, `offset: float`, `absolute_error=0.0`, and `declared_bounds` (the original lower/upper pair). It does not register the expression with a model. Encoding binary names are generated; preserve the metadata instead of parsing those names.

## Function: `encode_continuous`

```text
encode_continuous(continuous_var, num_bits=None, precision=None, max_bits=20)
```

Accept a `Continuous` with finite bounds and return an `Expression` on a finite binary grid. The direct helper defaults to eight bits when both resolution options are `None`; model compilation instead requires an explicit continuous resolution. `num_bits` and `precision` are mutually exclusive non-Boolean integers; `num_bits >= 1`, `precision >= 0`. `max_bits` is a non-Boolean integer in `[1, 53]`, limiting the required bit count.

With `num_bits=b`, the grid has `2**b` evenly spaced levels including both bounds. With `precision=p`, the step is `10**(-p)` starting at the lower bound, and the interval width must be an integer multiple of that step. A fixed domain returns a constant expression with no bits. Invalid domains/options and excess bits raise `EncodingError`; an unrepresentable step raises `NumericalStabilityError`.

`.encoding` contains `source`, `kind="continuous"`, `binaries: tuple[Binary, ...]`, `weights: tuple[float, ...]`, `offset: float`, and `absolute_error: float`. For a nonfixed domain it also contains `step: float` and `declared_bounds`; the error bound is half a grid step. A fixed domain records zero error and omits these last two fields. No model registration occurs.

## Function: `encode_fraction`

```text
encode_fraction(fraction_var)
```

Accept a `Fraction` and return an `Expression` covering its closed decimal grid exactly in the declared fixed-point units. Bounds must align to its `precision_decimals`; invalid domains or excess binary64 integer resolution raise `EncodingError`. A fixed variable uses no bits.

`.encoding` contains `source`, `kind="fractional"`, `binaries: tuple[Binary, ...]`, `weights: tuple[float, ...]`, `offset: float`, `absolute_error=0.0`, `step=10**(-precision_decimals)`, and `declared_domain` as `(lower_bound, upper_bound, precision_decimals)`. The representation uses binary64 arithmetic; zero declared discretization error does not imply arbitrary-precision decimal arithmetic. No model registration occurs.

## Function: `hubo_to_qubo`

```text
hubo_to_qubo(polynomial, *, strength=None, sense='MIN', auxiliary_prefix='__qpiai_product', matrix_format='symmetric', variable_names=None) -> qpiai_opt.opt_modeller.converters.HUBOToQUBOConvert
```

Accept a tuple-key binary polynomial dictionary using the [HUBO validation and canonicalization rules](api-formats.md#function-canonicalize_polynomial). Repeated factors collapse under `x*x=x`, duplicate monomials aggregate, `()` is the constant, and zero terms disappear. An empty or constant-only polynomial is accepted. Return `HUBOToQUBOConvert`; no solver runs.

`sense` is case-insensitive `"MIN"` or `"MAX"`, without whitespace stripping. MAX negates the source polynomial to form a minimizing target. `matrix_format` follows the [QUBO format rules](api-formats.md). `auxiliary_prefix` must be a nonempty string; generated names append `_<integer>` and avoid existing labels. With `variable_names=None`, source labels are ordered by `(type(label).__name__, repr(label))`. An explicit tuple/list must contain unique nonempty single-line strings covering every active source label; additional names retain isolated variables in the specified order.

The reduction repeatedly selects the most frequent pair among higher-order terms, breaking ties by the label order, and shares its auxiliary across all such terms. Each product `z=x*y` adds `strength*(x*y - 2*x*z - 2*y*z + 3*z)`. If `R` is the sum of absolute nonconstant coefficients after product substitution and before penalties, the minimum accepted strength is `max(1, R + max(1, R*1e-6))`. `None` selects that value; an explicit strength must be a finite real value at least that large, even when no auxiliary is needed. This preserves exact minimizing assignments; it does not guarantee a heuristic solver finds one.

Malformed polynomial data, initial canonicalization overflow, or invalid sense/prefix raise `ValueError`. Invalid names or matrix formats raise `InvalidQUBOError`. Insufficient strength or coefficient aggregation overflow during reduction raises `ModelCompilationError`; final QUBO formatting can raise `InvalidQUBOError`.

## Function: `qubo_to_milp`

```text
qubo_to_milp(qubo, *, num_variables=None, variable_names=None, constant=0.0, sense='MIN', name='QUBO_to_MILP') -> qpiai_opt.opt_modeller.converters.QUBOToMILPConvert
```

Return `QUBOToMILPConvert` containing an exact binary MILP linearization. `qubo` must be a dictionary with nonnegative non-Boolean integer pair keys and finite real coefficients. Mirrored pairs are summed as direct polynomial contributions; diagonal terms use `x*x=x`. Zero aggregate coefficients are removed. `constant` is a finite real objective offset.

`num_variables=None` infers one plus the largest supplied index, including indices on zero terms; an empty dictionary infers zero. An explicit non-Boolean integer must be at least that count and retains isolated variables. `variable_names=None` generates `x_0`, `x_1`, etc.; otherwise provide a tuple/list of exactly that many unique nonempty single-line strings. `sense` accepts case-insensitive `MIN`/`MAX` without stripping; `name` is the returned `Model` name. Source names must also satisfy the modeller's naming and collision rules, including generated product names.

For each nonzero off-diagonal coefficient, create a binary `z` and three rows `z<=x`, `z<=y`, `z>=x+y-1`. The objective preserves the constant and requested direction. No penalties, discretization, or solving occur. Invalid QUBO/count/name/constant data raise `InvalidQUBOError`; invalid sense raises `ValueError`; model naming or construction can raise typed modeller errors.

## Function: `milp_to_qubo`

```text
milp_to_qubo(model, **compile_options) -> qpiai_opt.opt_modeller.converters.MILPToQUBOConvert
```

Accept an algebraic `Model` with a linear objective and supported linear relations, and return `MILPToQUBOConvert`. Binary, bounded integer, and fixed-point variables are supported. Bounded continuous variables require explicit `continuous_bits` or `continuous_precision`; SOS, indicators, semi domains, and mixed domains are rejected. `compile_options` are the documented [`Model.to_qubo_triplets`](#method-to_qubo_triplets) options, including `matrix_format="symmetric"` by default, encoding resolutions, penalty controls, and compilation limits. Unknown options raise `TypeError`.

Compilation updates the supplied model's encoding and transformation state and retains that same model for decoding. The QUBO always minimizes the sense-normalized penalized energy; `original_sense` records the source direction. Keep the returned object and model together, decode the target sample, and check source feasibility before evaluating its objective. A different input type raises `TypeError`; unsupported structure, missing resolution, and encoding/compilation failures raise their typed modeller exceptions.

## Function: `qubo_to_ising`

```text
qubo_to_ising(qubo, *, num_variables=None, constant=0.0, variable_names=None) -> qpiai_opt.opt_modeller.converters.QUBOToIsingConvert
```

Return `QUBOToIsingConvert` using `x=(1+s)/2`. The `qubo`, `num_variables`, `constant`, and optional `variable_names` contracts match [`qubo_to_milp`](#function-qubo_to_milp), except omitted names remain `()` and maps then use integer indices. This is an energy-preserving substitution; the function has no source-sense argument and the result defaults to `original_sense="MIN"`.

For canonical direct coefficient `q_ii`, add `q_ii/2` to field `i` and the offset. For `q_ij` with `i<j`, add `q_ij/4` to coupling `(i,j)`, both fields, and the offset. Include the supplied constant exactly once. Aggregation overflow or a nonzero term underflowing during division raises `InvalidQUBOError`, as do malformed inputs.

## Function: `ising_to_qubo`

```text
ising_to_qubo(linear, quadratic, *, num_variables=None, constant=0.0, matrix_format='symmetric', variable_names=None, original_sense='MIN') -> qpiai_opt.opt_modeller.converters.IsingToQUBOConvert
```

Return `IsingToQUBOConvert` using `s=2*x-1` for the stored Ising energy. `linear` is an index-to-finite-real dictionary or an iterable of coefficients in index order. `quadratic` must be a dictionary of nonnegative non-Boolean integer pairs and finite real coefficients. Mirrored couplings aggregate; diagonal couplings become constants because `s*s=1`. `constant` is the finite Ising offset. Count inference, optional names, and zero-term index handling follow `qubo_to_ising`.

For canonical off-diagonal `J_ij`, the direct binary interaction is `4*J_ij`; each binary diagonal receives `2*h_i - 2*sum(J_ij incident to i)`. The offset becomes `constant - sum(h_i) + sum(J_ij)` after folding Ising diagonals. `matrix_format="symmetric"` splits direct binary interactions between mirrored entries; `"upper"` stores each once. `original_sense` accepts case-insensitive `MIN`/`MAX` without stripping and records interpretation only: it does not negate the supplied coefficients. For an original MAX objective, pass its minimizing Ising representation and record `MAX` so `source_objective()` restores the sign.

Invalid coefficients, indices, count, names, format, sense, or unrepresentable aggregation raise `InvalidQUBOError`. A noniterable `linear` argument raises `TypeError`.

## Class: `HUBOToQUBOConvert`

```text
HUBOToQUBOConvert(qubo: dict, offset: float, variable_names: tuple, original_variables: tuple, auxiliary_products: dict, strength: float, source_polynomial: dict, original_sense: str) -> None
```

Frozen record returned by `hubo_to_qubo`. `qubo: dict[tuple[int,int],float]` and `offset: float` describe the minimizing target; `variable_names: tuple` orders original labels followed by generated auxiliaries; `original_variables: tuple` contains only source labels. `auxiliary_products: dict[label,tuple[label,label]]` records products in construction order, `strength: float` is their common penalty, `source_polynomial: dict[tuple,float]` is the canonical unnegated source, and `original_sense` is `MIN` or `MAX`.

Obtain this object through the conversion function: direct dataclass construction does not validate consistency. Frozen field assignment does not freeze nested dictionaries. Binary samples may be sequences of source-variable length or full target length, or dictionaries covering all original labels or all original target indices. Extra dictionary entries are ignored. When integer labels allow both interpretations, disagreeing source values raise `ValueError`. Values must equal zero or one; Boolean bits are accepted. Missing source values raise `KeyError`; wrong sequence length or nonbinary values raise `ValueError`.

### Methods and properties

#### Property: `num_variables`

```python
num_variables
```

Return `int`: the number of original plus auxiliary variables in the quadratized QUBO.

#### Property: `index_to_variable`

```python
index_to_variable
```

Return a new `dict[int,label]` mapping QUBO indices to original and auxiliary labels.

#### Property: `variable_to_index`

```python
variable_to_index
```

Return a new inverse `dict[label,int]` mapping original and auxiliary labels to QUBO indices.

#### Property: `transformations`

```python
transformations
```

Return `list[dict]` with the Rosenberg product map, common penalty strength, source sense, and decoding rule.

#### Method: `complete_sample`

```text
complete_sample(sample)
```

Return `list[int]` in full `variable_names` order. Fill missing auxiliaries from their recorded products; preserve supplied auxiliary values even when inconsistent. Use `verify_auxiliaries()` to check a full assignment.

#### Method: `decode`

```text
decode(sample)
```

Return `dict[label,int]` containing only source variables. Accept the common source/full binary sample forms and discard auxiliaries without checking their product consistency.

#### Method: `verify_auxiliaries`

```text
verify_auxiliaries(sample)
```

Return `bool` indicating whether every auxiliary equals its recorded product. All source variables and all auxiliaries must be present; missing auxiliaries raise `KeyError`. With no auxiliaries, return `True`.

#### Method: `qubo_energy`

```text
qubo_energy(sample)
```

Return `float`: the target QUBO energy including `offset`. Missing auxiliaries are completed; inconsistent supplied auxiliaries retain their penalty contributions. Evaluation overflow raises `InvalidQUBOError`.

#### Method: `original_objective`

```text
original_objective(sample)
```

Return `float`: the original unnegated source polynomial evaluated on decoded source bits. Auxiliary penalties and auxiliary consistency do not affect this value. Evaluation overflow raises `ValueError`.

## Class: `QUBOToMILPConvert`

```text
QUBOToMILPConvert(model: qpiai_opt.opt_modeller.model.Model, original_variables: tuple, product_variables: dict, constant: float) -> None
```

Frozen record of a MILP reformulation and source-variable map for a QUBO. `model` is the exact binary MILP; `original_variables` is the ordered tuple of source `Binary` objects; `product_variables` maps each off-diagonal source index pair `(left, right)` to its auxiliary `Binary`; and `constant` is the source objective constant. Direct construction does not validate consistency; the retained model, variables, and nested dictionary remain mutable.

### Methods and properties

#### Method: `decode`

```text
decode(solution)
```

Accept a name-keyed solution mapping and return a new dictionary containing original variable names and their supplied values. This selects keys without validating binary domains or feasibility; a missing source name raises `KeyError`.

#### Property: `transformations`

```python
transformations
```

Return `list[dict]` recording the conversion, source index-pair strings mapped to auxiliary names, and the rule to retain original variables when decoding.

## Class: `MILPToQUBOConvert`

```text
MILPToQUBOConvert(qubo: dict, offset: float, variable_names: tuple, encoding_map: dict, original_sense: str, model: qpiai_opt.opt_modeller.model.Model) -> None
```

Frozen record returned by `milp_to_qubo`. `qubo: dict[tuple[int,int],float]` and `offset: float` are the minimizing compiled energy; `variable_names: tuple[str,...]` is target bit order; `encoding_map: dict` records source encodings; `original_sense` preserves `MIN`/`MAX`. `model` is the supplied source `Model`, not an independent snapshot. Direct construction does not validate consistency; nested dictionaries and the retained model remain mutable. Editing or recompiling that model can change later decoding and transformation results, so preserve its compiled state while interpreting this object.

### Methods and properties

#### Property: `num_variables`

```python
num_variables
```

Return `int`: the number of compiled QUBO variables.

#### Property: `index_to_variable`

```python
index_to_variable
```

Return a new `dict[int,str]` mapping compiled QUBO indices to variable names.

#### Property: `variable_to_index`

```python
variable_to_index
```

Return the inverse new `dict[str,int]` mapping compiled variable names to QUBO indices.

#### Method: `decode`

```text
decode(sample)
```

Delegate to the retained `model.decode_qubo_sample(sample)` and return its source-name-to-value dictionary. Its accepted sample forms, rounding, and failure rules apply. This does not establish source feasibility.

#### Property: `transformations`

```python
transformations
```

Return the retained model's current detached `list[dict]` of transformation records, using `model.transformations()`.

## Class: `QUBOToIsingConvert`

```text
QUBOToIsingConvert(linear: dict, quadratic: dict, offset: float, num_variables: int, variable_names: tuple = (), original_sense: str = 'MIN') -> None
```

Frozen indexed Ising record. Construction validates `num_variables` as a nonnegative non-Boolean integer, `linear` as an in-range index dictionary, `quadratic` as an in-range pair dictionary, and all coefficients/offset as finite reals. Mirrored couplings sum, diagonal couplings fold into `offset`, and zero aggregates disappear. Optional `variable_names` is empty or a tuple/list of exactly one unique nonempty single-line string per variable. `original_sense` is case-insensitive `MIN`/`MAX`, without stripping. Invalid data raise `InvalidQUBOError`.

Construction copies and normalizes coefficient dictionaries, but frozen field assignment does not freeze their contents. For `energy`, `decode`, and `source_objective`, provide a full-length spin sequence, an actual dictionary covering every index, or a dictionary covering every declared name. Mixed partial index/name coverage is insufficient. If both complete views exist they must agree; extras are ignored. Spins must be finite real -1/+1 values, excluding Booleans. Malformed samples raise `ValueError`; noniterable samples raise `TypeError`.

### Methods and properties

#### Property: `index_to_variable`

```python
index_to_variable
```

Return a new `dict[int,str | int]` mapping Ising indices to declared names, or to themselves when names are absent.

#### Property: `variable_to_index`

```python
variable_to_index
```

Return a new inverse `dict[str | int,int]` mapping declared names or unnamed indices to indices.

#### Property: `transformations`

```python
transformations
```

Return `list[dict]` recording `x=(1+s)/2`, the index map, offset, and source sense.

#### Method: `energy`

```text
energy(sample)
```

Return `float`: `offset + sum(h_i*s_i) + sum(J_ij*s_i*s_j)` for validated full-register spins. This evaluates the stored energy without changing its sign. Evaluation overflow raises `ValueError`.

#### Method: `decode`

```text
decode(sample)
```

Return `dict[str | int,int]` of declared names or indices to validated spins. This method returns -1/+1 spins; source binary bits can be recovered as `(s+1)//2`.

#### Method: `source_objective`

```text
source_objective(sample)
```

Return `float`: `energy(sample)` for source `MIN`, or its negation for source `MAX`. Correct restoration assumes stored coefficients describe the minimizing representation of that source objective.

#### Method: `to_qubo`

```text
to_qubo(*, matrix_format='symmetric') -> 'IsingToQUBOConvert'
```

Return `IsingToQUBOConvert` by applying `ising_to_qubo` to the stored energy, preserving count, names, and source-sense metadata. `matrix_format` selects symmetric or upper storage; it does not change the polynomial energy.

#### Method: `with_original_sense`

```text
with_original_sense(sense)
```

Return a new `QUBOToIsingConvert` with normalized case-insensitive `MIN`/`MAX` metadata, without negating coefficients. Construction revalidates and copies coefficient dictionaries. Invalid sense raises `ValueError`.

## Class: `IsingToQUBOConvert`

```text
IsingToQUBOConvert(qubo: dict, offset: float, num_variables: int, matrix_format: str = 'symmetric', variable_names: tuple = (), original_sense: str = 'MIN') -> None
```

Frozen record normally returned by `ising_to_qubo`. `qubo: dict[tuple[int,int],float]`, `offset: float`, and `matrix_format: str` describe the stored minimizing binary energy; `num_variables: int` includes isolated variables, `variable_names: tuple[str,...]` is empty or contains the declared source names, and `original_sense` preserves source interpretation. Direct dataclass construction does not validate these fields or their consistency. Nested dictionaries remain mutable.

Methods accept a full-length binary sequence or an actual dictionary covering every target index or every declared name. If both complete dictionary views exist they must agree; extra keys are ignored. Values must equal zero or one, and Boolean bits are accepted. Missing coverage, wrong length, conflicting views, or invalid bits raise `ValueError`; a noniterable sample raises `TypeError`.

### Methods and properties

#### Property: `index_to_variable`

```python
index_to_variable
```

Return a new `dict[int,str | int]` mapping QUBO indices to source Ising names or unnamed indices.

#### Property: `variable_to_index`

```python
variable_to_index
```

Return the inverse new `dict[str | int,int]` mapping source names or unnamed indices to QUBO indices.

#### Property: `transformations`

```python
transformations
```

Return `list[dict]` recording `s=2*x-1`, the index map, offset, and source sense.

#### Method: `energy`

```text
energy(sample)
```

Return `float`: the converted QUBO energy including `offset` for the validated binary sample. Evaluation overflow raises `InvalidQUBOError`.

#### Method: `decode`

```text
decode(sample)
```

Return `dict[str | int,int]` of source names or unnamed indices to validated binary values. To recover original Ising spins, apply `s=2*x-1` to these values.

#### Method: `source_objective`

```text
source_objective(sample)
```

Return `float`: `energy(sample)` for source `MIN`, or its negation for source `MAX`. This interprets the stored minimizing energy using the recorded source sense.

## Function: `hubo_to_ising`

```text
hubo_to_ising(polynomial, *, sense='MIN', **options) -> qpiai_opt.opt_modeller.converters.HUBOToIsingConvert
```

Apply `hubo_to_qubo(polynomial, sense=sense, **options)` followed by `qubo_to_ising` with its count, names, and offset. `options` accepts `strength`, `auxiliary_prefix`, `matrix_format`, and `variable_names` with the contracts above; unknown options raise `TypeError`. Return `HUBOToIsingConvert` retaining both transformations, the full Ising register, source binary decoding, source-objective evaluation, and `complete_sample()` for product auxiliaries. Polynomial, strength, coefficient, and sample errors follow those underlying converters. No solver runs.

## Class: `HUBOToIsingConvert`

```text
HUBOToIsingConvert(linear, quadratic, offset, num_variables,
                  variable_names=(), original_sense="MIN", *, quadratization)
```

The frozen dataclass returned by `hubo_to_ising`. Normally obtain it through that function so its Ising data and quadratization describe the same source polynomial. Fields `linear: dict[int, float]`, `quadratic: dict[tuple[int, int], float]`, `offset: float`, `num_variables: int`, `variable_names: tuple`, and `original_sense: str` follow `QUBOToIsingConvert`. Required `quadratization: HUBOToQUBOConvert` retains the binary source polynomial, product relations, and penalty strength.

| Member | Return | Meaning |
| --- | --- | --- |
| `index_to_variable` | `dict[int, label]` | Full register index-to-label map including auxiliaries. |
| `variable_to_index` | `dict[label, int]` | Inverse full-register map. |
| `transformations` | `list[dict]` | Ordered quadratization records followed by binary-to-spin substitution. |
| `energy(sample)` | `float` | Penalized Ising energy for a complete full-register spin sample. |
| `decode(sample)` | `dict[label, int]` | Validate full-register spins, map with `x=(1+s)/2`, and discard product auxiliaries. |
| `source_objective(sample)` | `float` | Evaluate the original binary polynomial on the decoded source sample, in its original sense. |
| `complete_sample(sample)` | `list[int]` | Accept a source binary sample, fill missing product auxiliaries, and return the full spin vector using `s=2*x-1`. |
| `to_qubo(*, matrix_format="symmetric")` | `IsingToQUBOConvert` | Convert the stored Ising energy back to QUBO. |
| `with_original_sense(sense)` | `HUBOToIsingConvert` | Return a new record with normalized `"MIN"` or `"MAX"` metadata. It retains the same `quadratization`; use the original conversion's sense when evaluating the retained source polynomial. |

Full spin samples may be sequences, index-keyed dictionaries, or dictionaries covering declared labels; spin values must be -1/+1. `complete_sample` instead accepts the binary sample contract of `HUBOToQUBOConvert`: a source/full sequence or a mapping covering source labels or source indices. Supplied auxiliary values are retained; use `quadratization.verify_auxiliaries()` on a binary sample to check them. Frozen field assignment does not freeze nested coefficient dictionaries.
