# Continuous quadratic programming

QpiAI Opt supports continuous quadratic objectives with linear constraints. Integer variables produce an unsupported MIQP, and quadratic constraints produce an unsupported QCQP.

## Mathematical form

```latex
$$
\begin{aligned}
\min\;\text{or}\;\max\quad &\tfrac12 x^TQx+c^Tx+c_0\\
\text{subject to}\quad &b_l\le Ax\le b_u,\\
&l\le x\le u,\quad x\in\mathbb R^n.
\end{aligned}
$$
```

The Hessian `Q` is symmetric. See the [matrix convention](#matrix-convention) for coefficients and storage.

## Build a convex QP

```python
from qpiai_opt.opt_modeller import Continuous, Model
from qpiai_opt.opt_modeller.utils.exceptions import ModelValidationError

x = Continuous("x", -10, 10)
y = Continuous("y", -10, 10)

model = Model("closest_feasible_point")
model.minimize((x - 2) ** 2 + (y + 1) ** 2 + 3)
model.add_constraint(x + y >= 0, label="half_space")

problem_class = model.validate()  # "QP"
```

Write a quadratic term as `x**2`. An expression such as `2**x` is exponential, not polynomial, and is outside this modeller's scope. Use an explicit piecewise-linear approximation when that is mathematically suitable.

MIN requires a positive-semidefinite Hessian. MAX requires a negative-semidefinite Hessian. A wrong-curvature or nonconvex objective raises `ModelValidationError`. A numerically indeterminate curvature check raises `NumericalStabilityError`; rescale or reformulate the objective.

```python
bad = Model("nonconvex")
bad.minimize(x**2 - y**2)

try:
    bad.to_qp_matrix()
except ModelValidationError as error:
    message = str(error)  # Nonconvex continuous QP ...
```

## Matrix convention

`to_qp_matrix()` represents the objective as

```text
0.5 * x.T @ Q @ x + c.T @ x + offset
```

Therefore a source term `a*x**2` becomes `Q[x,x] = 2*a`; a source term `a*x*y` becomes the symmetric pair `Q[x,y] = Q[y,x] = a`.

```python
matrix = model.to_qp_matrix()
hessian = matrix["objective_hessian"]
linear = matrix["objective"]
offset = matrix["objective_offset"]
curvature_tolerance = matrix["convexity_tolerance"]
```

`hessian`, `linear`, and `offset` are the `Q`, `c`, and constant terms in the stated matrix convention. `curvature_tolerance` is the tolerance used for the reported convexity classification.

`to_qp_matrix(*, sparse_matrix=True)` returns the [linear matrix dictionary](milp-modeller.md#matrix-representation) with the following changes and additions. `sparse_matrix` must be a Python `bool`. For `n` variables, `objective` has shape `(n,)` and `objective_hessian` has shape `(n, n)`.

| Field | Type and meaning |
| --- | --- |
| `problem_type` | `"QP"`. |
| `solver` | `str`: runtime adapter identifier. |
| `objective_hessian` | SciPy CSC `float64` matrix; NumPy `float64` array when `sparse_matrix=False`. |
| `objective_convention` | `"0.5 * x.T @ Q @ x + c.T @ x + offset"`. |
| `convex` | `True`, after successful source-sense curvature validation. |
| `global_optimum_if_optimal` | `True`: a proven optimum is global for this validated problem. |
| `hessian_extreme_eigenvalue` | `float`: smallest eigenvalue of `Q` for MIN or of `-Q` for MAX. |
| `convexity_tolerance` | `float`: `1e-9 * max(1, \|\|Q\|\|_inf)`. |

Coefficients and `sense` retain source coordinates; MAX objectives are not negated in the returned matrices. The constraint matrix remains CSR by default, or dense when requested. Successful export updates the model's transformation records with the Hessian convention and curvature check.

The returned matrix records `convex=True` and `global_optimum_if_optimal=True`. The latter means that an optimal solver status is a global result for the validated convex problem; exporting a matrix does not claim that a solve has already succeeded. If the smallest computed eigenvalue is negative but within the numerical uncertainty band, conversion asks you to rescale or reformulate the objective instead of assigning it a convex classification.

QpiAI Opt's local solver uses finite-precision feasibility and optimality tolerances. Very small curvature or objective differences can produce a materially different coordinate with a nearly identical objective. Scale the objective and verify the returned source coordinates when that distinction matters. The reported objective is recomputed from the source expression.

The general local numeric limits also apply: every nonzero Hessian or constraint coefficient must satisfy `1e-9 < abs(value) < 1e15`, and each linear objective coefficient and finite bound must have magnitude below `1e20`. See [Numeric input range](local-solving.md#numeric-input-range).

## Solve and verify

Continuous QP is local-only.

```python
import os

from qpiai_opt.opt_solvers import Client, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])

result = solve(model, time_limit=30, client=client)
solve_summary = {
    "problem_class": result.problem_class,
    "execution_mode": result.execution_mode,
    "status": result.status,
    "source_feasible": model.is_feasible(result.solution),
    "optimality_proven": result.info().optimality_proven,
    "objective": result.objective,
    "solution": result.solution,
    "compute_time_s": result.compute_time_s,
}
```

The optimum of this model is `x=2`, `y=-1`, with objective `3`. An optimal result reports `problem_class="QP"`, `execution_mode="local"`, `status="optimal"`, and `optimality_proven=True`. The SDK recomputes the objective from the source expression and returned coordinates. Check source feasibility and the result's termination before using the solution.

`mode="platform"` raises `CapabilityError` for QP because the current platform contract does not advertise that class.
