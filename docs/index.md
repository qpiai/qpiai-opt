# QpiAI Opt user manual

Start with installation and the modelling workflow, then use the chapter for the problem class returned by `classify(model)`. The API references define signatures, parameters, return types, and errors.

## Part I: Getting started

1. [Install, build a first model, and solve it](getting-started.md)
2. [Choose a problem class and modelling workflow](modelling-workflows.md)
3. [Choose local, platform, or local-first execution](local-solving.md#modes)
4. [Read solve result](local-solving.md#completed-result-contract)

The first complete workflow is:

```python
import os

from qpiai_opt.opt_modeller import Binary, Model
from qpiai_opt.opt_solvers import Client, classify, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])

x, y = Binary("x"), Binary("y")
model = Model("first_model")
model.maximize(5 * x + 3 * y)
model.add_constraint(x + y <= 1, label="choose_one")

problem_class = classify(model)  # "MILP"
result = solve(model, client=client)  # authenticated local execution; no paid work
objective = result.objective  # 5.0
assignment = result.solution  # {"x": 1.0, "y": 0.0}
```

## Part II: Modelling

### Shared modelling language

- [Variables, expressions, indexed variables, parameters, and updates](modelling-workflows.md)
- [Piecewise-linear interpolation with SOS2](piecewise-linear.md)
- [Model summary, validation, transformation inspection, and decoding](transformations.md)
- [Modeller limits and capacity](performance.md)
- [Canonical model IR and process boundaries](model-ir.md)

### Problem-class chapters

- [LP and MILP modeller](milp-modeller.md)
- [Continuous convex QP modeller](qp-modeller.md)
- [QUBO modeller and exact constraint penalties](qubo-modeller.md)
- [Native HUBO modeller](hubo-modeller.md)
- [Native Ising modeller](ising-modeller.md)

The modeller supports continuous QP with linear constraints and polynomial binary/spin objectives. It does not support MIQP, QCQP, general nonlinear programming, SDP, or conic models.

## Part III: Solving

### Common solving flow

- [Classification, local algorithms, limits, options, and routing](local-solving.md)
- [Problem-class results and common solve information](local-solving.md#completed-result-contract)
- [Platform client, jobs, resources, and account objects](platform-client.md)

### Solver-specific chapters

- [LP/MILP platform solver](milp-solver.md)
- [QUBO platform solver](qubo-solver.md)
- [HUBO platform solver](hubo-solver.md)
- [Ising solving](ising-modeller.md#solve)
- [Continuous QP local solving](qp-modeller.md#solve-and-verify)
- [Gate-based optimization](quantum-optimization.md)

Local QUBO, HUBO, and Ising use their native representations. HUBO is not converted to MILP or QUBO for either platform or local solving.

## Part IV: Interoperability and automation

- [Standard files, dictionaries, QUBO/Ising conversions, and OMMX](interoperability.md)
- [Canonical JSON IR](model-ir.md)
- [Automation workflows](../automation.md)

## Part V: Reference and project operation

- [Public Python API index](public-api.md)
- [Gate-based optimization algorithm, configuration, and result API](api-quantum.md)
- [Representation utility signatures](api-formats.md)
- [Result schemas and units](local-solving.md#completed-result-contract)
- [Contributing, tests, releases, and supported Python versions](../CONTRIBUTING.md)
- [Private security reporting](../REPORTING_BUGS.md)
- [Structured citation metadata](../CITATION.cff)
- [Feature requests](../FEATURE_REQUESTS.md)
