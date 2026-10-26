# External solver interoperability

Exchange models through LP/MPS files, linear matrices, polynomial dictionaries, or the optional OMMX bridge. The table identifies inputs accepted directly by external APIs and exchanges that require an adapter, model reconstruction, or explicit reformulation.

## Supported exchanges

| QpiAI output | Target | Integration | Usage and requirements |
|---|---|---|---|
| `.lp`, `.mps`, `.lp.gz`, `.mps.gz` | HiGHS | **directly compatible** | `highspy.Highs().readModel(path)`; compressed input requires a build with zlib support |
| `.lp.gz`, `.mps.gz` | QpiAI local solve | **directly compatible through the SDK** | pass the compressed path to `solve(path, client=client)` |
| `.lp`, `.mps`, `.lp.gz`, `.mps.gz` | Gurobi | **directly compatible** | `gurobipy.read(path)`; compressed input requires the corresponding compression utility on the host; prefer MPS when column order and numeric fidelity matter |
| `.lp`, `.mps`, `.lp.gz`, `.mps.gz` | IBM CPLEX | **directly compatible** | `cplex.Cplex(path)` or `cpx.read(path)` |
| `.mps` | PuLP | **requires MPS adaptation** | `LpProblem.fromMPS(path)` requires explicit restoration of the source objective and sense. PuLP 4.2 omits the objective constant; 3.3.2 rejects objective-row RHS entries, which must be removed before import. Decompress gzip input first |
| `.lp` / `.mps` | Pyomo | **requires model reconstruction** | Pyomo writes these formats and delegates them to solvers, but does not document a general reader that reconstructs a Pyomo model from either file; rebuild the model or use the file directly with a downstream solver |
| QpiAI model or linear matrix dictionary | CVXPY | **requires model reconstruction** | create CVXPY variables, constraints, and objective explicitly; CVXPY has no documented QpiAI, LP, or MPS importer |
| linear matrix dictionary | SciPy `milp` | **requires an adapter** | pass the objective vector as `c`, preserve `integrality`, and construct `Bounds` and `LinearConstraint` from the variable and row bounds. Negate MAX costs for minimization and restore the source objective constant |
| linear matrix dictionary | Gurobi matrix API | **requires an adapter** | create an `MVar`, then pass the sparse matrix and row bounds through `addMConstr`/linear constraints; field names and ranged-row handling differ |
| linear model | OR-Tools `MPSolver` | **requires model reconstruction** | OR-Tools exports LP/MPS and loads its own proto, but its Python API does not provide a general LP/MPS model reader; rebuild variables/rows or translate through another library |
| upper QUBO dictionary + offset | D-Wave dimod | **directly compatible** | `dimod.BinaryQuadraticModel.from_qubo(upper_qubo, offset)` |
| symmetric QUBO dictionary + offset | D-Wave dimod | **directly compatible** | `from_qubo(symmetric_qubo, offset)` adds mirrored half-coefficients into one interaction |
| upper QUBO dictionary | OpenJij | **directly compatible** | `oj.SASampler().sample_qubo(upper_qubo)`; constant offset is tracked separately because sampling does not need it |
| symmetric QUBO dictionary | OpenJij | **directly compatible** | `sampler.sample_qubo(symmetric_qubo)` aggregates mirrored half-coefficients into one interaction; retain the constant offset separately |
| `IsingProblem.linear`, `.quadratic` | D-Wave dimod samplers | **directly compatible** | `sampler.sample_ising(ising.linear, ising.quadratic)`; add `ising.offset` when comparing reported energies |
| `IsingProblem.linear`, `.quadratic` | OpenJij | **directly compatible** | `sampler.sample_ising(h, J)`; preserve the offset and source sense separately |
| HUBO polynomial dictionary | OpenJij | **directly compatible** | `SASampler.sample_hubo(polynomial, vartype="BINARY")` |
| HUBO polynomial dictionary | dimod polynomial API | **directly compatible** | `dimod.BinaryPolynomial(poly, "BINARY")` represents the polynomial directly; `dimod.ExactPolySolver().sample_hubo(poly)` enumerates small problems |
| HUBO polynomial dictionary | dimod `HigherOrderComposite` (via quadratic reduction) | **compatible through the composite** | `dimod.HigherOrderComposite(sampler).sample_hubo(poly, penalty_strength=strength)` reduces the polynomial internally before calling the quadratic sampler. A sufficient `strength` is required to preserve minimizing source assignments |
| QUBO | Qiskit Optimization | **requires explicit conversion** | construct a `QuadraticProgram`; its Ising translator expects a Pauli operator, not QpiAI dictionaries |
| HUBO polynomial dictionary | Qiskit Optimization `QuadraticProgram` (via explicit quadratic reduction) | **requires quadratization and conversion** | reduce to QUBO first, then construct a `QuadraticProgram`; this model does not accept higher-order objective terms directly |
| QpiAI canonical IR | external package | **requires an adapter** | the versioned schema preserves SDK model semantics; external consumers need a schema adapter |
| QpiAI `Model` Python object | external modeller/solver | **requires an adapter** | export a documented representation; symbolic classes are package-specific |

## OMMX bridge

Install the optional stable OMMX v2 API:

```bash
python -m pip install "qpiai-opt[ommx]"
```

The extra requires `ommx>=2.6,<3`. OMMX 2.9.2 publishes wheels for Windows x86-64, Linux x86-64/AArch64, and Apple Silicon macOS, with no Intel macOS wheel in that release; see the [OMMX 2.9.2 release files](https://pypi.org/project/ommx/2.9.2/). The base SDK and its standard LP/MPS, polynomial, and IR exchange formats remain available on Intel macOS without this extra. Dependency requirements and wheel availability belong to the selected OMMX release.

Export and import with the same mathematical data:

```python
from qpiai_opt.opt_modeller import from_ommx, to_ommx

instance = to_ommx(model)
restored = from_ommx(instance)
```

The bridge preserves objective polynomial and constant, MIN/MAX sense, binary domains, integer/continuous/semi-continuous/semi-integer domains and bounds, and linear equality/inequality constraints. Binary imports use the full `{0, 1}` domain; express a fixed binary value as an explicit linear equality before calling `from_ommx()`. It rejects Spin, Fraction, Mixed, logical, indicator, SOS, custom-penalty, and nonlinear relational constraints instead of weakening or approximating them. `Model.to_ommx()` and `Model.from_ommx()` are convenience equivalents; `convert(model, "OMMX")` is available to automation.

Partial assignments, solution objects, fixed binary bounds, variable dependencies, removed constraints, constraint hints, named functions, subscripts, and metadata beyond imported titles and names are currently unsupported.

On import, variable IDs, variable names, and constraint labels must each be unique. Missing variable names become `x[id]`; missing constraint labels become `c{id}`. Generated names must also be unique, and OMMX subscripts do not distinguish duplicate names.

```python
instance = model.to_ommx()
variable_ids = model.transformations()[-1]["variable_ids"]
```

`variable_ids` maps each QpiAI variable name to the integer ID used by the returned OMMX instance.

OMMX is not required for local or platform solves. Calling `to_ommx()`, `from_ommx()`, or their `Model` equivalents without the extra raises `ImportError` with a message naming `qpiai-opt[ommx]`. The bridge targets OMMX 2.6's documented `ommx.v1.Instance`, `DecisionVariable`, `Constraint`, and `Polynomial` API: [OMMX v1 Python reference](https://jij-inc.github.io/ommx/python/ommx/autoapi/ommx/v1/index.html).

## QUBO representation rule

External samplers minimize their input energy. Preserve constants and source sense alongside the exchanged coefficients. `Model.to_qubo_dict()` already produces a minimizing representation. When exchanging source-coordinate coefficients with `sense="MAX"`, negate the objective coefficients and offset before minimization, then restore the source objective when interpreting energies.

The direct polynomial convention stores each interaction once:

```python
upper_qubo, offset = model.to_qubo_dict(matrix_format="upper")

import dimod

bqm = dimod.BinaryQuadraticModel.from_qubo(upper_qubo, offset)
```

The default `matrix_format="symmetric"` is the QpiAI Platform convention. For an interaction coefficient `q`, it stores `q/2` at both `(i, j)` and `(j, i)`. dimod's `from_qubo` adds contributions for the same undirected interaction, so both SDK formats preserve the same energy. Use upper output when another reader requires each pair to appear only once. Do not keep only one triangle of symmetric output without adding the mirrored contribution.

## LP/MPS portability

`Model.to_lp_file()` and `to_mps_file()` write solver-neutral linear models, including integrality and SDK reformulations for indicators, semi domains, and SOS constraints. Portability applies to the emitted mathematical model; it does not promise preservation of every target-specific feature, option, basis, callback, or solution-pool setting.

Prefer MPS for machine interchange and LP for inspection. Gurobi documents that LP can lose column order and exact coefficient values. MPS likewise cannot carry all modelling-library metadata, so use canonical IR when QpiAI-specific labels, parameter bindings, or transformation metadata must round-trip. The SDK writes finite numeric values with round-trip decimal precision, but the target reader still determines the numeric semantics of the imported text.

Within the SDK, the same plain or gzip path works on Linux, macOS, and Windows, including Unicode path components. Direct calls to an external package remain subject to that package's build and path capabilities.

## Exchange formats

Supported exchange routes are OMMX, LP/MPS, canonical IR, QUBO and HUBO dictionaries, and Ising fields and couplings. Direct object conversion for Gurobi, SCIP, CPLEX, Python-MIP, and similar packages is not part of the public API; use one of the documented exchange representations.
