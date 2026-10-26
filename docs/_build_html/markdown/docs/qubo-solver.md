# QUBO solver

`QUBOSolver` validates and submits QUBO problems, supports blocking and nonblocking job workflows, and returns typed results.

## Construct a platform problem

### From a modeller

```python
from qpiai_opt.opt_modeller import Binary, Model
from qpiai_opt.opt_solvers.qubo import QUBOProblem

x = Binary("x")
y = Binary("y")

model = Model("selection")
model.maximize(3 * x + 2 * y - 4 * x * y + 5)

problem = QUBOProblem.from_model(model)
```

This is the preferred bridge. It carries:

- the fully symmetric compiled matrix;
- source minimization/maximization sense;
- source constant;
- compiled variable names;
- compiled variable count.

Compilation happens immediately and retains an independent source-model snapshot for decoding, objective evaluation, feasibility, and transformations. Later edits to the original `Model` do not affect the compiled problem. Compile again to include those edits.

`max_evaluations` can be supplied to `from_model()`. Continuous source variables require the same explicit QUBO compile options:

```python
problem = QUBOProblem.from_model(
    model,
    continuous_bits=10,
    max_evaluations=50_000,
)
```

### Direct symmetric matrix

```python
problem = QUBOProblem(
    num_variables=2,
    qubo_dict={
        (0, 0): 3.0,
        (1, 1): 2.0,
        (0, 1): -2.0,
        (1, 0): -2.0,
    },
    variable_names=("x", "y"),
    offset=5.0,
    sense="MAX",
)
```

For `matrix_format="symmetric"` (default):

- every nonzero off-diagonal key needs its mirror;
- mirrored values must be equal;
- each mirrored value is half the direct polynomial coefficient;
- zero entries are removed after validation.

Missing or unequal mirrors raise `pydantic.ValidationError`.

### Direct upper-triangular coefficients

```python
problem = QUBOProblem(
    num_variables=2,
    qubo_dict={
        (0, 0): 3.0,
        (1, 1): 2.0,
        (0, 1): -4.0,
    },
    matrix_format="upper",
    variable_names=("x", "y"),
    offset=5.0,
    sense="MAX",
)

stored_format = problem.matrix_format
mirrored_coefficients = (
    problem.qubo_dict[(0, 1)],
    problem.qubo_dict[(1, 0)],
)
```

`stored_format == "symmetric"` and `mirrored_coefficients == (-2.0, -2.0)`: the direct upper coefficient is split evenly across the symmetric matrix.

The model validates the upper triangle and immediately stores the platform form. Lower-triangle keys are invalid in upper input. If halving a nonzero coefficient underflows to zero, construction raises rather than losing the interaction.

## `QUBOProblem` fields

| Field | Default | Contract |
| --- | --- | --- |
| `num_variables` | required | Strict nonnegative integer; zero represents a constant problem locally. |
| `qubo_dict` | required | Finite `(int, int)` coefficient dictionary; indices lie in `[0, num_variables)`. |
| `matrix_format` | `"symmetric"` | `"symmetric"` or `"upper"` at input; normalized to symmetric storage. |
| `max_evaluations` | `10000` | Strict positive integer sent to the platform. |
| `problem_type` | `"QUBO"` | Fixed QUBO marker. |
| `offset` | `0.0` | Finite source-objective constant retained by the SDK; not sent as a matrix term. |
| `sense` | `"MIN"` | `"MIN"` minimizes the source objective; `"MAX"` maximizes it. Results are restored to this source sense. |
| `variable_names` | `None` | One unique non-empty name per index; used for named results. |

Unknown fields are forbidden.

An empty normalized matrix is valid for local zero-energy solving when `num_variables` is supplied. The platform cannot store that representation, so `QUBOSolver.submit()`, high-level platform solving, and `submit()` reject it before creating a platform problem.

```python
import os

from qpiai_opt.opt_solvers import Client, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])
zero_energy = QUBOProblem(num_variables=3, qubo_dict={})
local_result = solve(zero_energy, client=client)
zero_energy_solution = local_result.solution  # [0, 0, 0]

# Raises CapabilityError before a platform request.
solve(zero_energy, mode="platform", client=client)
```

## Client and solver construction

```python
import os

from qpiai_opt.opt_solvers import Client
from qpiai_opt.opt_solvers.qubo import QUBOSolver

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])
solver = QUBOSolver(client=client)
```

The solver can also create its own client:

```python
import os

solver = QUBOSolver(
    api_key=os.environ["QPIAI_OPT_API_KEY"],
    experiment_name="Default Experiment",
)
```

Ordinary calls omit `compute_resource_id`; the platform automatically chooses an eligible QUBO resource. Advanced callers may pin a discovered resource ID on the solver or an individual call, where the per-call value takes precedence. A compute-resource ID is the opaque `id` on a record returned by `Client.list_solvers()`:

```python
resources = client.list_solvers(method="QUBO", active_only=True)
resource = resources[0]

pinned_submission = solver.submit(
    problem,
    compute_resource_id=resource.id,
)
```

This placement pins only that submission. Supplying the ID to `QUBOSolver(..., compute_resource_id=resource.id)` makes it the solver default.

```python
available = solver.health()
```

`health() -> bool` delegates to `Client.health()`. It performs an authenticated account request with a five-second timeout, creates no problem or job, and returns `False` on request or API failure.

## Blocking solve

Blocking solves read the current job state, then wait through Server-Sent Events (SSE) by default. If the stream cannot complete monitoring, the waiter falls back to polling; `poll_interval` controls that fallback interval in seconds.

```python
result = solver.solve(
    problem,
    name="Selection QUBO",
    experiment_name="Benchmarks",
    timeout=3600,
    request_timeout=60,
    poll_interval=5,
)
```

Execution sequence:

1. validate and serialize the problem;
2. create a persisted platform problem;
3. submit the job;
4. wait for a terminal job state;
5. retrieve inline or storage-backed output;
6. validate and restore source metadata.

`timeout` is the waiting budget, starting after submission; it excludes problem creation, submission, and final result retrieval. `request_timeout` defaults to the client's timeout and supplies the connection/read timeout for those requests. Status reads use the client's timeout within the waiter's remaining budget. These HTTP timeouts bound connection and read inactivity, not total transfer duration; an in-flight request can overrun the waiting budget. A local timeout stops waiting without cancelling the platform job. Resume monitoring the same job ID rather than resubmitting. See [request and wait timing](platform-client.md#timeouts).

## Nonblocking lifecycle

This solver-instance `submit()` method is explicitly platform-only. Supplying its `Client` and calling it is the caller's choice to create one potentially paid platform job.

```python
submission = solver.submit(
    problem,
    name="Selection QUBO",
    timeout=60,
)

submission_fields = submission.model_dump(mode="json")
```

`submit()` returns after platform submission; it never waits for completion. Its `timeout` is an individual request bound. `submission_fields` contains the new `job_id`, `problem_id`, normalized lifecycle `status`, and platform `message`.

Check state:

```python
status = solver.get_status(submission.job_id, timeout=60)

if status.status == "completed":
    result = status.result
elif status.status == "failed":
    failure_message = status.error
```

`get_status()` includes the validated result when completed.

Retrieve explicitly:

```python
result = solver.get_result(submission.job_id, timeout=60)
solution = solver.get_solution(result=result)
```

`get_result()` raises `RuntimeError` unless the job is completed. `get_solution(job_id=...)` retrieves the result first; passing an existing result avoids another request.

## Result interpretation

```python
result_summary = {
    "num_nodes": result.num_nodes,
    "compute_time_s": result.compute_time_s,
    "solution": result.solution,
    "objective": result.objective,
    "variable_assignment": result.variable_assignment,
    "method": result.info().method,
    "guarantee": result.info().guarantee,
}
```

| Field | Meaning |
| --- | --- |
| `problem_class` | Always `"QUBO"`. |
| `execution_mode` | `"local"` or `"platform"`. |
| `job_id` | Platform job identifier retained by blocking and retrieval workflows. |
| `problem_id` | Platform problem identifier associated with the job, when returned. |
| `solution` | Binary list in QUBO index order. |
| `variable_assignment` | Source-name dictionary when a source `Model` is retained; otherwise explicit variable names or generated `x[index]` names. |
| `objective` | Source objective evaluated from the returned bitstring after sense and offset restoration when metadata is available. |
| `num_nodes` | Non-negative QUBO node count; local results use the QUBO variable count. |
| `compute_time_s`, `solver_time_s` | Compute and algorithm durations in seconds, or `None` when unavailable. |
| `compute_time_ms`, `compute_time_us`, `solver_time_ms`, `solver_time_us` | Runtime in milliseconds and microseconds; omitted from default serialization. |

`result.info()` reports the provider, method, termination, source feasibility, optimality proof, guarantee, transformation, and known settings and is backed by the serializable `solve_info` field. For a source-feasible candidate, local `exhaustive-enumeration` reports `termination="optimal"` and `guarantee="global_optimum"`; simulated annealing and platform execution report `"completed"` and `"best_found"`. A decoded candidate that violates its source model has no usable-solution guarantee. `get_status()` separately returns asynchronous job lifecycle state and embeds the result only after completion.

When source metadata is available, the SDK also checks the returned bitstring against the reported matrix energy. This comparison excludes the source offset and reverses the matrix sign for MAX, matching the minimizing service coordinates.

Metadata is cached only in the `QUBOSolver` instance that submitted the job. For retrieval in another process, reconstruct/pass the original problem:

```python
result = solver.get_result(
    submission.job_id,
    problem=problem,
)
```

Without `problem`, the SDK cannot recover an offset, source sense, or variable names absent from the platform response. `objective` then contains the reported minimizing matrix value; `info().source_objective` and `info().transformed` remain `None`. Supply the original problem when retrieving source coordinates.

For a source-aware platform result, the reported minimizing matrix objective must agree with the returned bitstring under `round(abs(reported - recomputed)) <= max(1, 1e-7 * max(1, abs(reported), abs(recomputed)))`. A non-finite difference or a rounded difference above this tolerance raises `RuntimeError`. Python rounding uses ties to even. When the tolerance is one, a difference of `1.4` is accepted and `1.5` is rejected; the tolerance increases with objective magnitude. The adapter then restores the source offset, sense, and decoded assignment.

For a modeller source, decode and verify independently:

```python
decoded = model.decode_qubo_sample(result.solution)
source_feasible = model.is_feasible(decoded)
recomputed_objective = model.evaluate_objective(decoded)
```

`source_feasible` confirms every source constraint. Compare `recomputed_objective` with `result.objective` using a tolerance for non-integral coefficients.

## Problem names and experiments

Problem names are labels and need not be unique. If omitted, the SDK generates a UTC timestamped name:

```text
SDK QUBO problem - <UTC timestamp>
```

Per-call experiment ID/name overrides the solver/client fallback. Names are matched exactly within the authenticated organization and cached after resolution.

## Submission failures

Problem/config/identifier validation occurs before the corresponding network side effect.

If the platform definitively rejects job submission with a non-transient 4xx, the solver attempts to delete the new problem record. For HTTP 408, 425, 429, server failures, or transport errors, acceptance is ambiguous. The record is retained and the exception note tells the caller which problem to inspect.

Do not blindly resubmit an ambiguous paid job:

```python
jobs = client.list_problem_jobs(problem_id, page=1, page_size=20)
```

`solve()` raises `JobFailedError`, `JobCancelledError`, or `TimeoutError` when the waiter reaches those outcomes. HTTP and validation failures use `APIError`, `AuthenticationError`, Requests exceptions, or `pydantic.ValidationError`.

The result tables above define every solver-specific field; common result and error semantics are in [Local solving](local-solving.md) and [Automation](../automation.md). [Construct a platform problem](#construct-a-platform-problem) shows modeller-to-solver and independently supplied QUBO input, and [blocking solve](#blocking-solve) shows the explicit paid submission boundary.
