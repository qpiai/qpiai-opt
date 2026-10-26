# HUBO solver

QpiAI's native higher-order HUBO solver works directly with the problem's natural higher-order polynomial, preserving its energy landscape without quadratization or reduction-induced approximations. `HUBOSolver` validates and submits the polynomial, preserves source labels, and returns typed results.

## Construct `HUBOProblem`

```python
from qpiai_opt.opt_solvers.hubo import HUBOProblem

problem = HUBOProblem(
    polynomial_dict={
        (): 4.0,
        ("x",): -2.0,
        ("x", "y"): 3.0,
        ("x", "y", "z"): -5.0,
    },
    num_variables=3,
    sense="MIN",
)
```

Polynomial rules:

- every key is a tuple;
- `()` is the constant term;
- labels are non-empty strings;
- coefficients are finite real numbers;
- repeated labels within a term are removed by binary idempotence;
- term labels are sorted deterministically;
- equivalent terms are aggregated and zero terms are removed;
- `num_variables` is a nonnegative integer;
- without `variable_names`, it must equal the distinct label count;
- optional `variable_names` declares the complete register, including isolated variables.

Local execution supports zero and constant polynomials and isolated variables. The platform HUBO service requires a nonempty polynomial with an active term and every declared variable present in it; unsupported platform inputs are rejected before submission.

Thus:

```python
canonical = HUBOProblem(
    polynomial_dict={
        ("y", "x", "x"): 2,
        ("x", "y"): -1,
    },
    num_variables=2,
)

canonical.polynomial_dict  # {("x", "y"): 1.0}
```

### From an algebraic model

```python
from qpiai_opt.opt_modeller import Binary, Model

x, y, z = Binary("x"), Binary("y"), Binary("z")
model = Model("cubic_assignment")
model.minimize(4 - 2 * x + 3 * x * y - 5 * x * y * z)
problem = HUBOProblem.from_model(model)
```

`from_model()` compiles the polynomial and retains an independent source snapshot for variable decoding, objective evaluation, and feasibility checks. Later edits to `model` do not change that problem. Encoding and penalty options follow [HUBO compilation](hubo-modeller.md).

## Problem fields

| Field | Default | Contract |
| --- | --- | --- |
| `polynomial_dict` | required | Validated binary polynomial, including a zero polynomial locally. |
| `num_variables` | required | Strict nonnegative integer equal to the declared register size. |
| `variable_names` | `None` | Optional complete, ordered, unique label tuple. |
| `sense` | `"MIN"` | `"MIN"` minimizes the source polynomial; `"MAX"` maximizes it. Results are restored to this source sense. |
| `problem_type` | `"HUBO"` | Fixed HUBO marker. |
| `vartype` | `"BINARY"` | Only supported HUBO variable type. |
| `solver_config` | `None` | Validated solver-parameter dictionary. |

Unknown fields are forbidden.

## Client and solver construction

```python
import os

from qpiai_opt.opt_solvers import Client
from qpiai_opt.opt_solvers.hubo import HUBOSolver

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])
solver = HUBOSolver(client=client)
```

The solver can create its own client with `api_key=...`, `url=...`, `experiment_id=...`, or `experiment_name=...`.

```python
available = solver.health()
```

`health() -> bool` delegates to `Client.health()`. It performs an authenticated account request with a five-second timeout, creates no problem or job, and returns `False` on request or API failure.

## Solver configuration

Configuration can be stored on the problem:

```python
problem = HUBOProblem(
    polynomial_dict={("x",): -1.0},
    num_variables=1,
    solver_config={
        "num_reads": 100,
        "num_sweeps": 1000,
        "seed": 42,
    },
)
```

or passed to `solve()`/`submit()`:

```python
result = solver.solve(
    problem,
    num_reads=100,
    num_sweeps=1000,
    seed=42,
)
```

Per-call values override the same keys in `problem.solver_config`.

| Parameter | Purpose | Validation |
| --- | --- | --- |
| `num_sweeps` | Annealing sweeps per read. | Positive integer. |
| `num_reads` | Number of independent sampling reads. | Positive integer. |
| `num_threads` | Requested solver thread count. | Positive integer. |
| `beta_min`, `beta_max` | Lower and upper inverse-temperature endpoints for annealing. | Positive finite numbers; `beta_min <= beta_max` when both are present. |
| `temperature_schedule` | Linear or geometric progression between the annealing endpoints. | `"LINEAR"` or `"GEOMETRIC"`, case-insensitive at input. |
| `seed` | Random seed for sampling. | Integer. |

Numeric settings reject Boolean values. Unknown keys raise before problem creation. `solver_config=...` may be passed as one explicit dictionary to a solver call; recognized direct keyword values are merged over it.

For `qpiai_opt.opt_solvers.solve(problem, mode="local", client=client)`, the native local solver uses stored `num_reads`, `num_sweeps`, and `seed`; direct call values override them. The remaining settings in the table are platform-only and a local solve or inspection rejects them instead of ignoring them. Small local problems use exhaustive enumeration, so annealing settings do not affect that solve and are omitted from `result.info().settings`.

## Blocking solve

Blocking solves read the current job state, then wait through Server-Sent Events (SSE) by default. If the stream cannot complete monitoring, the waiter falls back to polling; `poll_interval` controls that fallback interval in seconds.

```python
result = solver.solve(
    problem,
    name="Cubic assignment",
    experiment_name="Benchmarks",
    num_reads=100,
    seed=42,
    timeout=3600,
    request_timeout=60,
    poll_interval=5,
)
```

`solve()`:

1. validates/canonicalizes the polynomial and solver options;
2. creates the persisted HUBO problem;
3. submits a job;
4. waits for a terminal job state;
5. retrieves and validates inline/storage-backed output;
6. restores source labels when the map is available.

`timeout` is the waiting budget, starting after submission; it excludes problem creation, submission, and final result retrieval. `request_timeout` defaults to the client's timeout and supplies the connection/read timeout for those requests. Status reads use the client's timeout within the waiter's remaining budget. These HTTP timeouts bound connection and read inactivity, not total transfer duration; an in-flight request can overrun the waiting budget. A local timeout stops waiting without cancelling the platform job. Resume monitoring the same job ID rather than resubmitting. See [request and wait timing](platform-client.md#timeouts).

## Nonblocking lifecycle

This solver-instance `submit()` method is explicitly platform-only. Supplying its `Client` and calling it is the caller's choice to create one potentially paid platform job.

```python
submission = solver.submit(
    problem,
    name="Cubic assignment",
    num_reads=100,
    seed=42,
    timeout=60,
)

status = solver.get_status(submission.job_id, timeout=60)
if status.status == "completed":
    result = status.result
```

`submit()` returns `job_id`, normalized initial `status`, acknowledgement `message`, and `problem_id`. It does not wait for solver execution.

```python
result = solver.get_result(
    submission.job_id,
    timeout=60,
    problem=problem,
)
solution = solver.get_solution(result=result)
```

`get_result()` requires a completed job and otherwise raises `RuntimeError`. `get_solution(job_id=...)` performs retrieval; pass `result=...` to avoid another request.

## Label serialization

Labels may be nonempty single-line strings, including punctuation and Unicode, but cannot contain NUL, carriage return, or newline. Completed results restore the original labels when the solver has the source problem. For retrieval in another process, pass that problem explicitly:

```python
result = solver.get_result(submission.job_id, problem=problem)
```

Without the source problem, the result retains the labels supplied by the platform and cannot reconstruct information that was not returned.

## Result interpretation

| Field | Meaning |
| --- | --- |
| `problem_class` | Always `"HUBO"`. |
| `execution_mode` | `"local"` or `"platform"`. |
| `job_id` | Platform job identifier retained by blocking and retrieval workflows. |
| `problem_id` | Platform problem identifier associated with the job, when returned. |
| `num_variables` | Nonnegative compiled binary-variable count. |
| `solution` | Binary list in declared `variable_names` order, or canonical label order when names were omitted. |
| `variable_assignment` | Decoded source-name-to-value mapping for a model-derived problem; otherwise label-to-binary values. |
| `objective` | Source objective, including model decoding when available; otherwise the reported solver objective. |
| `compute_time_s`, `solver_time_s` | Compute and algorithm durations in seconds, or `None` when unavailable. |
| `compute_time_ms`, `compute_time_us`, `solver_time_ms`, `solver_time_us` | Runtime in milliseconds and microseconds. |

Use the common view when method or guarantee affects downstream use:

```python
info = result.info()
solve_summary = (info.method, info.termination, info.guarantee)
```

Local `method` is `"exhaustive-enumeration"` or `"simulated-annealing"`; the platform method is `"HUBO"`. For a source-feasible candidate, exact enumeration reports `termination="optimal"` and `guarantee="global_optimum"`; annealing and platform execution report `"completed"` and `"best_found"`. A decoded candidate that violates its source model has no usable-solution guarantee. See [common solve information](local-solving.md#common-solve-information).

The response validator requires exactly `num_variables` solution entries and binary values.

Verify the returned assignment locally:

```python
from qpiai_opt.opt_modeller.formats import calculate_hubo_energy

energy = calculate_hubo_energy(
    problem.polynomial_dict,
    result.variable_assignment,
)
```

Compare this value with `result.objective` using an appropriate floating-point tolerance. Pass `problem=problem` when retrieving from another process so the SDK can restore labels and evaluate the source polynomial. When that metadata is available, a reported solver objective that disagrees with the returned assignment raises instead of producing a contradictory result. The typed `solve_info` field stores execution details in ordinary dumps. `result.info()` returns the common solver and guarantee view. Without the source problem, `objective` contains the reported solver objective while `info().source_objective` and `info().transformed` are `None`. `get_status()` separately returns asynchronous job lifecycle state and embeds this result only after completion.

For a source-aware result, every reported platform objective must agree with the returned assignment under `round(abs(reported - recomputed)) <= max(1, 1e-7 * max(1, abs(reported), abs(recomputed)))`. A non-finite difference or a rounded difference above this tolerance raises `RuntimeError`. Python rounding uses ties to even. When the tolerance is one, a difference of `1.4` is accepted and `1.5` is rejected; the tolerance increases with objective magnitude.

`get_status()` returns `job_id`, normalized lifecycle state, optional result/error/timestamps, and the available `compute_time_s` and `solver_time_s` measurements.

## Problem names, resources, and experiments

Problem names need not be unique. An omitted name becomes a UTC timestamped `SDK HUBO problem - ...` label.

Ordinary calls omit `compute_resource_id`; the platform automatically chooses an eligible HUBO resource. Advanced callers may pin a discovered resource ID. The ID is the opaque `id` of an active resource returned by solver discovery:

```python
resources = client.list_solvers(method="HUBO", active_only=True)
resource = resources[0]

pinned_submission = solver.submit(
    problem,
    compute_resource_id=resource.id,
)
```

Pass the same value to `HUBOSolver(..., compute_resource_id=resource.id)` to make it the default for that solver instance. Per-call `experiment_id` and exact `experiment_name` override solver/client defaults. If no experiment is configured, the client uses `Default Experiment`.

## Failure and cleanup behavior

All problem and solver configuration validation occurs locally.

A definitive non-transient 4xx submission rejection triggers cleanup of the new problem record. Timeout-like status codes, server failures, and transport errors have ambiguous acceptance; the SDK retains the problem and attaches its ID to the exception notes. Inspect existing jobs before resubmitting.

Blocking `solve()` can raise:

- `pydantic.ValidationError` for invalid problem/result models;
- `ValueError` for unsupported solver parameters;
- `AuthenticationError` or `APIError` for platform responses;
- Requests exceptions for transport failures;
- `JobFailedError` or `JobCancelledError`;
- `TimeoutError` for the total wait deadline.

The result tables above define the solver-specific schema; common status and failure semantics are in [Automation](../automation.md). The [problem construction](#construct-huboproblem) section shows both a polynomial compiled by `Model` and a direct polynomial supplied without the modeller.
