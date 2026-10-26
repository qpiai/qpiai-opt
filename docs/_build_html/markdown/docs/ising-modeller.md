# Ising modeller

The SDK supports native spin expressions, indexed coefficient dictionaries, and exact QUBO conversion. Local solving works directly in the spin domain. Explicit platform execution converts to the platform's QUBO representation and restores spins and the source objective.

## Mathematical form

An Ising problem uses spins $s_i \in \{-1,+1\}$ and the objective

```latex
$$
E(s)=c+\sum_i h_i s_i+\sum_{i<j}J_{ij}s_i s_j.
$$
```

## Model and compile

```python
from qpiai_opt.opt_modeller import Model, create_spin_array

s, t = create_spin_array("spin", 2)
model = Model("native_ising")
model.maximize(2 * s - 3 * s * t + 4)
problem = model.compile()
ising_data = model.to_ising_dict()
```

`compile()` returns an `IsingProblem` with an independent source snapshot. `to_ising_dict()` returns its mathematical fields as a dictionary, including `problem_type`, variable count, source `sense`, offset, names, fields, and couplings. Both minimization and maximization retain the source coefficients in that dictionary. The solver performs any required minimization normalization internally.

A native spin model contains only `Spin` variables and no stored constraints. Repeated powers reduce by parity: $s_i^2=1$, so even powers are constants and odd powers retain one spin. After parity reduction, a term may contain at most two distinct spins. Higher-order spin interactions are rejected. `Spin(name)` creates one variable; `create_spin_array(prefix, size)` creates deterministic names and permits size zero.

## IsingProblem

```python
from qpiai_opt.opt_solvers.ising import IsingProblem

problem = IsingProblem(
    num_variables=2,
    linear={0: 2.0, 1: -1.0},
    quadratic={(0, 1): -3.0},
    offset=4.0,
    sense="MAX",
    variable_names=("left", "right"),
)
```

| Field | Meaning |
| --- | --- |
| `problem_type` | Always `"ISING"`. |
| `num_variables` | Non-negative register size. |
| `sense` | `"MIN"` or `"MAX"`, with coefficients in source coordinates. |
| `offset` | Finite source-objective constant. |
| `variable_names` | Empty tuple or one unique non-empty name per spin. |
| `linear` | Indexed finite fields; zero fields are omitted. |
| `quadratic` | Finite pair couplings, aggregated to `i < j`. |

Self-couplings contribute to the offset because $s_i s_i=1$. Mirrored entries aggregate into one pair. Input dictionaries are copied during validation. Invalid indices, counts, names, coefficients, offsets, senses, or aggregation overflow raise.

`IsingProblem.from_model(model, **compile_options)` is also available. A binary or supported encoded source model first compiles to QUBO, then converts to Ising. Its source snapshot retains the encodings, constraint penalties, variable order, source sense, and transformations. Native spin sources accept no binary-encoding options.

## Evaluate and decode

```python
sample = {"left": 1, "right": -1}
energy = problem.energy(sample)
assignment = problem.decode(sample)
source_objective = problem.source_objective(sample)
```

Samples may be a sequence of exactly `num_variables` spins, an index-keyed dictionary, or a dictionary covering all declared names. Simultaneously supplied index and named values must agree. Missing or out-of-domain values raise.

`energy()` evaluates the stored spin polynomial in its source coordinates. For a model-derived problem, `decode()` restores the original variables, including binary and numeric encodings; `source_objective()` evaluates that original model. For a directly constructed spin problem, the source objective is its energy.

## Solve

```python
import os

from qpiai_opt.opt_solvers import Client, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])

result = solve(problem, client=client)
```

Local solving uses exhaustive enumeration within the exact work limit and native spin-domain annealing above that cutoff. `solve(problem, mode="platform", client=client)` explicitly uses the optimization platform's QUBO solver. `result.solution` remains a spin list in the compiled register order. `variable_assignment` contains decoded source variables when available. `objective` is the source objective, including its offset and original direction.

Every result has the shared identity and `solver_time_s`/`compute_time_s` header. Platform identifiers are `None` locally. `solve_info` serializes the provider, method, feasibility, termination, and guarantee; `info()` returns that interpretation. Spin solution lists are the last serialized field.

## QUBO to Ising

`qubo_to_ising()` applies $x=(1+s)/2$ and returns `QUBOToIsingConvert`:

```python
from qpiai_opt.opt_modeller import qubo_to_ising

source_qubo = {(0, 0): -3.0, (0, 1): 4.0, (1, 1): -2.0}
converted = qubo_to_ising(
    source_qubo,
    num_variables=2,
    constant=7.0,
    variable_names=("x", "y"),
)
spin_energy = converted.energy([1, -1])
```

The converter retains fields, couplings, offset, register size, names, bidirectional index/name maps, transformation records, and spin-sample validation. It interprets the QUBO dictionary exactly as stored: upper-triangular entries contain full interactions, while symmetric entries contain half on each mirrored position. `num_variables` preserves isolated trailing variables; otherwise it is inferred from the largest index. Conversion underflow that would erase a nonzero coefficient raises.

Conversion records describe a minimizing energy. `original_sense` records the source direction, and `source_objective()` restores that direction. `with_original_sense()` returns a new record with validated source sense. This convention belongs to conversion records; native `IsingProblem` coefficients use their declared source sense directly.

The automation operation returns a dictionary suitable for `IsingProblem`:

```python
from qpiai_opt.opt_solvers import convert
from qpiai_opt.opt_solvers.qubo import QUBOProblem

qubo_problem = QUBOProblem(
    qubo_dict=source_qubo,
    matrix_format="upper",
    num_variables=2,
    offset=7.0,
    variable_names=("x", "y"),
)
ising_data = convert(qubo_problem, "ISING")
problem = IsingProblem(**ising_data)
```

## Ising to QUBO

`ising_to_qubo()` applies $s=2x-1$. `convert(problem, "QUBO", matrix_format="upper")` normalizes a native problem's source sense and returns `IsingToQUBOConvert`:

```python
converted_qubo = convert(problem, "QUBO", matrix_format="upper")
qubo = converted_qubo.qubo
offset = converted_qubo.offset
```

The conversion record retains `qubo`, `offset`, `num_variables`, `matrix_format`, names and index maps, `original_sense`, transformation records, and `energy()`, `decode()`, and `source_objective()` methods. Its samples are binary. The default matrix format is symmetric; `"upper"` stores each interaction once. Its minimizing QUBO energy equals the normalized Ising energy, including the offset; `source_objective()` restores the original direction.

`QUBOToIsingConvert.to_qubo()` supports the corresponding conversion-record round trip. `HUBOToIsingConvert`, returned by `hubo_to_ising()`, also retains the exact quadratization, source binary decoding, and product-auxiliary completion.
