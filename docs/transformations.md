# Model inspection, conversions, and decoding

## Human-readable model inspection

`summary()` returns concise text; `pretty_print()` displays and returns the same text. `max_constraints` limits only the displayed constraint list.

```python
from qpiai_opt.opt_modeller import Binary, Model

x, y = Binary("x"), Binary("y")
model = Model("selection")
model.minimize(-3 * x - 2 * y)
model.add_constraint(x + y <= 1, label="capacity")

summary = model.summary(max_constraints=5)
displayed_summary = model.pretty_print(max_constraints=5)
```

Both values contain the detected problem class, variable-domain counts, objective, and named constraints. `repr(model)` remains a one-line identity view.

## Transformation records

Operations that change representation record the applied variable maps, coefficients, penalties, offsets, and auxiliary variables.

```python
qubo, offset = model.to_qubo_dict(matrix_format="upper")
steps = model.transformations()
latest_step = steps[-1]
```

Records can describe domain encodings, source-to-binary weights, sense and offset normalization, constraint penalties, slack and auxiliary variables, piecewise SOS2 formulation, QP curvature validation, QUBO/Ising substitution, and OMMX variable IDs. `transformations()` returns a detached copy; changing the returned dictionaries does not change the model.

## Conversion map

| Source | Target | Operation | Returned value |
| --- | --- | --- | --- |
| `Model` | QUBO | `model.to_qubo_dict()` | `(qubo_dict, offset)`; decoding remains on `model` |
| `Model` | HUBO | `model.to_hubo_dict()` | polynomial dictionary including its constant |
| `Model` | Ising | `model.to_ising_dict()` | `dict` |
| `Model` | LP/MPS | `model.to_lp_string()`, `model.to_mps_string()` | solver-neutral text |
| `Model` | canonical IR | `to_ir(model)` | JSON-compatible dictionary |
| `Model` or `HUBOProblem` | qubit Hamiltonian | `convert(value, "QUBIT_HAMILTONIAN")` | `QubitHamiltonian` |
| `Model` | OMMX | `to_ommx(model)` | OMMX `Instance` |
| HUBO | Ising | `hubo_to_ising()` | `HUBOToIsingConvert` |
| qubit Hamiltonian | QUBO or IR | `convert(value, "QUBO")`, `convert(value, "IR")` | `IsingToQUBOConvert` or IR dictionary |
| HUBO | QUBO | `hubo_to_qubo()` | `HUBOToQUBOConvert` |
| QUBO | MILP | `qubo_to_milp()` | `QUBOToMILPConvert` |
| MILP `Model` | QUBO | `milp_to_qubo()` | `MILPToQUBOConvert` |
| QUBO | Ising | `qubo_to_ising()` | `QUBOToIsingConvert` |
| Ising | QUBO | `ising_to_qubo()` or `ising.to_qubo()` | `IsingToQUBOConvert` |

A conversion object carries the transformed representation and the information needed to decode a target assignment. Preserve that object until the target result has been interpreted.

## HUBO to QUBO

`hubo_to_qubo()` replaces higher-order products with shared binary auxiliaries and a validated product penalty.

```python
from qpiai_opt.opt_modeller import hubo_to_qubo

source_hubo = {
    (): 3.0,
    ("x",): -2.0,
    ("x", "y", "z"): 5.0,
}

reduced = hubo_to_qubo(
    source_hubo,
    sense="MIN",
    matrix_format="upper",
)

source_sample = {"x": 1, "y": 0, "z": 1}
full_sample = reduced.complete_sample(source_sample)

auxiliaries_valid = reduced.verify_auxiliaries(full_sample)
source_value = reduced.original_objective(full_sample)
target_value = reduced.qubo_energy(full_sample)
decoded = reduced.decode(full_sample)
```

`full_sample` follows `reduced.variable_names` and includes implied auxiliary bits. For a valid auxiliary assignment, `target_value` equals the sense-normalized source energy and `decoded` contains only `x`, `y`, and `z`. `strength=None` chooses the minimum safe penalty. An explicit strength below that value raises `ModelCompilationError`.

For `sense="MAX"`, the returned QUBO is a minimizing representation of the negated source polynomial. `reduced.original_sense` records `"MAX"` and `reduced.original_objective(sample)` evaluates the original, unnegated polynomial.

## QUBO to MILP

`qubo_to_milp()` replaces every off-diagonal binary product with one auxiliary binary variable and exact linear product constraints.

```python
import os

from qpiai_opt.opt_modeller import qubo_to_milp
from qpiai_opt.opt_solvers import Client, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])

source_qubo = {
    (0, 0): -3.0,
    (0, 1): 4.0,
    (1, 1): -2.0,
}

linearized = qubo_to_milp(
    source_qubo,
    num_variables=2,
    variable_names=("x", "y"),
    constant=7.0,
    sense="MIN",
)

milp_result = solve(linearized.model, client=client)
source_sample = linearized.decode(milp_result.solution)
```

The input is a direct polynomial dictionary: store each off-diagonal interaction once, or split it equally across mirrored keys. The returned model preserves the constant and `MIN`/`MAX` direction. `decode()` removes product variables and returns the original binary names.

## MILP to QUBO

`milp_to_qubo()` supports linear objectives and relations over binary, bounded integer, fixed-point, and explicitly discretized bounded continuous variables. SOS, indicators, semi domains, and mixed domains are rejected.

```python
from qpiai_opt.opt_modeller import Binary, Model, milp_to_qubo
from qpiai_opt.opt_solvers import solve
from qpiai_opt.opt_solvers.qubo import QUBOProblem

x = Binary("x")
y = Binary("y")

source_model = Model("binary_selection")
source_model.minimize(-3 * x - 2 * y)
source_model.add_constraint(x + y <= 1, label="capacity")

compiled = milp_to_qubo(source_model, matrix_format="upper")

target_problem = QUBOProblem(
    num_variables=compiled.num_variables,
    qubo_dict=compiled.qubo,
    matrix_format="upper",
    offset=compiled.offset,
    sense="MIN",
    variable_names=compiled.variable_names,
)

qubo_result = solve(target_problem, client=client)
source_solution = compiled.decode(qubo_result.solution)
source_feasible = source_model.is_feasible(source_solution)
source_objective = source_model.evaluate_objective(source_solution)
```

`compiled.qubo` is always the minimizing target energy. The conversion records the source direction in `compiled.original_sense`; use the retained `source_model` or `compiled.model` to evaluate the decoded source objective. `source_feasible` must be true before using the decoded candidate.

## QUBO to Ising

`qubo_to_ising()` applies `x = (1 + s) / 2` and preserves the constant, variable count, and names.

```python
from qpiai_opt.opt_modeller import qubo_to_ising
from qpiai_opt.opt_modeller.formats import calculate_qubo_energy

upper_qubo = {
    (0, 0): -3.0,
    (0, 1): 4.0,
    (1, 1): -2.0,
}

ising = qubo_to_ising(
    upper_qubo,
    num_variables=2,
    constant=7.0,
    variable_names=("x", "y"),
)

binary_sample = {"x": 1, "y": 0}
spin_sample = {name: 2 * value - 1 for name, value in binary_sample.items()}

qubo_value = calculate_qubo_energy(upper_qubo, [1, 0], constant=7.0)
ising_value = ising.energy(spin_sample)
```

`qubo_value` and `ising_value` are equal, subject only to floating-point roundoff. A symmetric QUBO input must contain half of each direct interaction on both mirrored keys.

## Ising to QUBO

`ising_to_qubo()` and `QUBOToIsingConvert.to_qubo()` apply `s = 2x - 1`.

```python
round_trip = ising.to_qubo(matrix_format="upper")

round_trip_value = round_trip.energy(binary_sample)
binary_assignment = round_trip.decode(binary_sample)
source_value = round_trip.source_objective(binary_sample)
```

`round_trip_value` equals `ising_value`. `binary_assignment` uses the original Ising names. `source_objective()` returns the original objective direction: it equals the energy for `MIN` and its negation for `MAX`.

## High-level conversion

`qpiai_opt.opt_solvers.convert()` provides the same supported routes when a workflow selects a representation by name.

```python
from qpiai_opt.opt_solvers import convert

model_ir = convert(source_model, "IR")
qubo_data = convert(source_model, "QUBO", matrix_format="upper")
hubo_data = convert(source_model, "HUBO")
ising_model = convert(target_problem, "ISING")
restored_qubo = convert(ising_model, "QUBO", matrix_format="upper")
```

Targets are case-insensitive. Supported `Model` targets are `"IR"`, `"QUBO"`, `"HUBO"`, `"ISING"`, `"LP"`, `"MPS"`, `"QUBIT_HAMILTONIAN"`, and `"OMMX"`. Hamiltonian export requires the gate-based optimization integration. A `QUBOProblem` converts to Ising, a `HUBOProblem` converts to QUBO, and an `IsingProblem` converts to QUBO. Unsupported source/target pairs and options raise `ValueError`. The [conversion reference](api-solvers.md#conversion-routes) lists all native-problem routes and options.

## Specialized exact QUBO penalties

The QUBO compiler recognizes three slack-free binary structures:

```text
x <= y                 -> x(1-y)
sum(x[i]) <= 1         -> sum(i<j, x[i]x[j])
sum(x[i]) >= n-1       -> sum(i<j, (1-x[i])(1-x[j]))
```

Each penalty is zero exactly on feasible binary assignments and positive on every infeasible assignment. Other inequalities use the general slack encoding.

```python
from qpiai_opt.opt_modeller import Binary, Model

a, b, c = Binary("a"), Binary("b"), Binary("c")
penalty_model = Model("at_most_one")
penalty_model.minimize(-a - b - c)
penalty_model.add_constraint(a + b + c <= 1)

penalty_model.to_qubo_dict(matrix_format="upper")
step = penalty_model.transformations()[-1]

rewrite = step["rewrite"]
auxiliary_variables = step["auxiliary_variables"]
```

For this at-most-one relation, the rewrite uses pairwise products and `auxiliary_variables` is empty.

## Decode compiled model samples

A `Model` compiled directly to QUBO or HUBO retains its encoder map.

```python
source_values = source_model.decode_qubo_sample(qubo_result.solution)
objective = source_model.evaluate_objective(source_values)
violations = source_model.constraint_violations(source_values)
feasible = source_model.is_feasible(source_values)
```

Use `feasible` and `violations` before accepting a heuristic candidate. `objective` is always evaluated in the source `MIN`/`MAX` direction.
