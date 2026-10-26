# Piecewise-linear relationships

`Model.add_piecewise_linear()` adds an exact piecewise-linear interpolation graph using continuous convex-combination weights and SOS2 adjacency. It does not evaluate an arbitrary Python function or create a general nonlinear model.

## Interpolate breakpoints

```python
from qpiai_opt.opt_modeller import Continuous, Model

x = Continuous("load", 0, 2)
y = Continuous("cost", 0, 4)
model = Model("piecewise_cost")

relationship = model.add_piecewise_linear(
    x,
    y,
    breakpoints=[0, 1, 2],
    values=[0, 1, 4],
    name="cost_curve",
)
model.minimize(y)
model.add_constraint(x >= 1.5, label="required_load")
```

For weights `lambda[i]`, the method adds all four required links:

```text
sum(lambda[i]) = 1
x = sum(breakpoint[i] * lambda[i])
y = sum(value[i] * lambda[i])
SOS2(lambda in breakpoint order)
```

Weights are continuous in `[0, 1]`. SOS2 permits at most two adjacent nonzero weights. Together these equations describe exactly the line segments between the supplied points, including nonconvex piecewise-linear graphs.

## Solve and inspect

SOS2 is reformulated exactly for local execution, and `classify(model)` reports `"MILP"` for this continuous piecewise model.

```python
import os

from qpiai_opt.opt_solvers import Client, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])

result = solve(model, client=client)
result.solution["load"]  # 1.5
result.solution["cost"]  # 2.5
weight_values = [result.solution[v.name] for v in relationship.weights]
source_feasible = model.is_feasible(result.solution)  # True
transformation = model.transformations()[0]
```

`weight_values == [0.0, 0.5, 0.5]`: the solution lies halfway between the second and third breakpoints. `transformation` records the source and output variables, breakpoints, values, and generated weight names.

Feasibility checks accept either a complete solver assignment or source variables only. When the generated weights are omitted, the model derives the unique adjacent interpolation weights from the supplied source coordinate:

```python
model.is_feasible({"load": 1.5, "cost": 2.5})  # True
model.is_feasible({"load": 1.5, "cost": 2.4})  # False
```

Explicitly supplied weight values are still validated. Canonical IR preserves the generated-weight role and validates that the metadata, linking equations, and SOS2 set agree before restoring source-only feasibility behavior.

Breakpoints must be finite and strictly increasing; values must be finite and have matching length. Source and output must be distinct decision variables. Their declared domains apply: an `Integer` source restricts the graph to integer source coordinates, while interpolation weights remain continuous. The method represents continuous piecewise-linear graphs.
