# Indexed variables, updates, and parameters

Use indexed variables to construct structured models, then update coefficients, bounds, and right-hand sides through the same `Model`. The next conversion or solve uses the updated values.

## Expression arithmetic

Build algebra with ordinary Python operators or `quicksum()`:

```python
from qpiai_opt.opt_modeller import Binary, quicksum

x = Binary("x")
y = Binary("y")

linear = quicksum([3 * x, -2 * y, 5])
quadratic = 4 * x * y
```

Equivalent terms and constants are combined according to their mathematical sum, independent of insertion order. This applies to linear, quadratic, and higher-order expressions. Scalar multiplication and division act on each combined coefficient once, so the result does not depend on whether equivalent terms were entered separately or as one term. Dividing a `Variable` or `Expression` by zero raises `ZeroDivisionError`; a non-real or non-finite divisor raises `InvalidExpressionError`. Polynomial multiplication first combines each input and then combines products that produce the same monomial. Numeric values use IEEE-754 binary64 arithmetic: ordinary decimal rounding is expected, and the SDK does not provide arbitrary-precision arithmetic. Keep coefficients and assignments within a practical numeric scale; values outside binary64's finite range are outside the supported input domain. See [numeric representation](model-ir.md#numeric-representation) for the shared contract.

Every scalar variable exposes its canonical domain through `var_type`:

```python
x.var_type  # "BINARY"
```

Use `var_type` when a generic modelling routine must branch on `BINARY`, `SPIN`, `INTEGER`, `CONTINUOUS`, `SEMI_CONTINUOUS`, `SEMI_INTEGER`, `FRACTIONAL`, or `MIXED`. The class names `SemiContinuous`, `SemiInteger`, and `Fraction` differ from these domain strings.

## Multidimensional variables

```python
from qpiai_opt.opt_modeller import Binary, IndexedVariables, Model

x = IndexedVariables(
    Binary,
    "ship",
    ["north", "south"],
    range(24),
    ["standard", "express"],
)

model = Model("shipping")
model.add_indexed_variables(x)
model.minimize(x.sum())
model.add_constraint(x.sum("north", "*", "express") <= 8)

one = x["south", 9, "standard"]
indexed_names = {key: variable.name for key, variable in x.items()}
```

`one.name == "ship[south,9,standard]"`. `indexed_names` contains one entry for each Cartesian-product index in deterministic order.

```python
IndexedVariables(variable_type, name, *index_sets, **variable_options)
```

| Argument | Contract |
|---|---|
| `variable_type` | subclass of scalar `Variable`, such as `Binary`, `Integer`, or `Continuous` |
| `name` | nonempty base name without line breaks, commas, or brackets |
| `index_sets` | one or more iterables containing unique simple strings or finite numbers |
| `variable_options` | forwarded to every scalar variable constructor, for example `lower_bound=0, upper_bound=10` |

Names are deterministic Cartesian-product names such as `ship[north,0,express]`. Index text must be a simple string/number without NUL, newline, comma, or brackets; values that would make names ambiguous are rejected. NaN and infinity are rejected because they do not provide reliable lookup and equality semantics.

An empty axis produces an empty Cartesian product while preserving `shape` and `index_sets`. Iteration, selection, and `values()` are empty, and `sum()` is the constant zero. This supports data-driven models in which a valid category has no members.

| Member | Return |
|---|---|
| `shape` | tuple of axis lengths |
| `index_sets` | immutable tuple of axis tuples |
| `x[key]` | scalar variable; a one-dimensional collection accepts `x[key]`, higher dimensions require a tuple |
| iteration | keys in Cartesian-product order |
| `keys()` / `values()` / `items()` | mapping views or key-variable pairs in that order |
| `get(key, default=None)` | matching scalar variable, or `default` when the key is absent |
| `select(*pattern)` | tuple of variables; `None` or `"*"` is a wildcard for one axis |
| `sum()` | symbolic sum of every member |
| `sum(*pattern)` | symbolic sum of a selected slice |

Wrong dimensionality raises `KeyError` for indexing and `ValueError` for selection. `Model.add_indexed_variables()` registers members even when their current coefficient is zero.

## Direct updates

| Method | Accepted target/value | Return | Rejection |
|---|---|---|---|
| `set_objective_coefficient(variable_or_name, coefficient)` | model variable or its name; finite scalar or `Parameter` | normalized `float`, or the supplied `Parameter` after binding | unknown variable, missing objective, nonfinite value |
| `set_constraint_rhs(constraint, value)` | constraint index or effective label; finite scalar or `Parameter` | normalized `float`, or the supplied `Parameter` after binding | non-relational target, unknown target, nonfinite value |
| `set_variable_bounds(variable_or_name, *, lower_bound=None, upper_bound=None)` | existing non-binary/non-spin variable or its name | mutated scalar variable | invalid domain bounds, fixed binary/spin domains, unsupported mixed domain |

Objective coefficient replacement affects the linear one-variable term only; it does not rewrite coefficients inside nonlinear terms. RHS replacement also supports the relation nested in an indicator.

Omitted bounds retain their current values. Every update uses the same domain validation as construction.

## Parameters

```python
from qpiai_opt.opt_modeller import Continuous, Model, Parameter

model = Model("dispatch")
power = model.add_variable(Continuous("power", 0, 100))
model.minimize(power)
model.add_constraint(power >= 20, label="demand")

price = Parameter("price", 4.5)
demand = Parameter("demand", 20)

model.set_objective_coefficient(power, price)  # registers and binds
model.bind_parameter(demand, target="constraint_rhs", constraint="demand")

model.set_parameter("price", 5.25)
model.set_parameter(demand, 35)
```

`Parameter(name, value)` requires a nonempty name and finite non-Boolean real value. Names must fit on one line because they are persisted in canonical IR. `set(value)` mutates and returns the normalized float; `as_dict()` returns `{"name": name, "value": value}`.

`add_variables(iterable)` validates the whole batch before registering any member. A bad item or a distinct variable that reuses an existing name leaves the model unchanged. Piecewise-linear construction has the same all-or-nothing mutation contract.

### Binding

```text
model.add_parameter(parameter)
model.bind_parameter(
    parameter_or_name,
    *,
    target,
    variable=None,
    constraint=None,
) -> dict
```

| `target` | Required selector | Effect |
|---|---|---|
| `objective_coefficient` | `variable` | replaces its linear objective coefficient |
| `constraint_rhs` | `constraint` index or label | replaces scalar RHS |
| `lower_bound` | `variable` | replaces lower bound |
| `upper_bound` | `variable` | replaces upper bound |

Registering a different `Parameter` object under an existing name is rejected. Duplicate identical bindings are ignored. Binding immediately applies the current parameter value.

`set_parameter(parameter_or_name, value)` validates the value, applies all bindings, and returns the normalized `float`. If any application fails, the parameter and already-applied bindings are restored to the prior value before the exception is raised. `Parameter.set()` changes the parameter alone; use `Model.set_parameter()` when its bound model fields must change too.

When one parameter controls both bounds of the same variable, the SDK validates and applies the final lower/upper pair atomically. For example, a variable fixed at `[2, 2]` can be moved to `[7, 7]` without a false intermediate lower-greater-than-upper failure. Multiple variables and all other binding targets retain the same all-or-nothing rollback contract.

Parameters and bindings round-trip through canonical IR. They are not sent as server-side symbolic objects; each solve compiles current concrete values.
