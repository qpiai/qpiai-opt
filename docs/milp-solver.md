# MILP solver

`MILPSolver` uploads an LP or MPS file, creates a platform problem, submits the job, and validates the solver response. It accepts `.lp`, `.mps`, `.lp.gz`, and `.mps.gz` files, case-insensitively. Paths may contain Unicode characters on Linux, macOS, and Windows.

Use the [MILP modeller](milp-modeller.md) to create a file from an SDK model, or submit a compatible file produced elsewhere.

## Client and solver construction

```python
import os

from qpiai_opt.opt_solvers import Client
from qpiai_opt.opt_solvers.milp import MILPSolver

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])
solver = MILPSolver(client=client)
```

The solver can instead construct its own client from `api_key=...`, `url=...`, `experiment_id=...`, or `experiment_name=...`.

```python
available = solver.health()
```

`health() -> bool` delegates to `Client.health()`. It performs an authenticated account request with a five-second timeout, creates no problem or job, and returns `False` on request or API failure.

## Solver configuration

Only set parameters whose defaults you intend to override:

```python
from qpiai_opt.opt_solvers.milp import MILPSolverConfig

config = MILPSolverConfig(
    time_limit=120,
    mip_rel_gap=0.001,
    threads=4,
    presolve="choose",
    solver_algorithm="choose",
    output_flag=False,
)
```

Unknown fields are forbidden. Booleans are not accepted as numeric values, all floating-point parameters must be finite, and integer fields marked as strict do not accept floats.

### Termination

| Field | Default | Valid values |
| --- | ---: | --- |
| `time_limit` | `300.0` | Finite seconds greater than or equal to zero; zero requests no solver limit. |
| `mip_max_nodes` | `-1` | Strict integer at least `-1`; `-1` means unlimited. |
| `solution_limit` | `-1` | Stop after this many feasible MIP solutions; `-1` means unlimited and positive integers set the limit. |
| `objective_bound` | `None` | Optional finite dual-bound stopping threshold; for MIN, stop when the lower bound reaches or exceeds it. |
| `objective_target` | `None` | Optional finite incumbent target; stop when a feasible solution reaches this value or better. |

### Gaps and feasibility tolerances

| Field | Default | Valid values |
| --- | ---: | --- |
| `mip_rel_gap` | `1e-4` | Finite non-negative relative incumbent-to-bound stopping gap; `1e-4` is 0.01%. |
| `mip_abs_gap` | `1e-6` | Finite non-negative absolute incumbent-to-bound stopping gap. |
| `primal_feasibility_tolerance` | `1e-7` | Finite positive tolerance for primal constraint feasibility. |
| `dual_feasibility_tolerance` | `1e-7` | Finite positive tolerance for LP dual feasibility. |
| `mip_feasibility_tolerance` | `1e-6` | Finite positive integrality tolerance: accepted integer values satisfy `abs(x - round(x)) <= tolerance`. |

### Algorithm and output

| Field | Default | Valid values |
| --- | ---: | --- |
| `threads` | `0` | Strict non-negative integer; zero delegates selection to the solver. |
| `presolve` | `"choose"` | `"on"` enables presolve, `"off"` disables it, and `"choose"` lets the selected solver decide. Input is case-insensitive. |
| `solver_algorithm` | `"choose"` | `"simplex"` selects a simplex method, `"ipm"` selects an interior-point method, and `"choose"` lets the selected solver decide. Input is case-insensitive. |
| `mip_heuristic_effort` | `0.05` | Controls solver effort spent on MIP heuristics; finite value from zero through one. |
| `output_flag` | `False` | Strict Boolean; enables solver console output when `True`. |
| `log_file` | `None` | Optional solver-side log path string. |

### Optional result features

| Field | Default | Purpose |
| --- | ---: | --- |
| `include_dual_info` | `False` | Requests dual values and reduced costs. |
| `compute_ranging` | `False` | Requests sensitivity ranges. |
| `compute_iis` | `True` | Requests an IIS when the model is infeasible. |
| `collect_solution_pool` | `False` | Requests improving MIP solutions. |

These four fields require strict booleans. Whether a requested feature is available depends on the selected platform solver and the problem class. The SDK validates the shape of returned optional data; it does not manufacture missing solver diagnostics.

## Warm starts

A partial incumbent supplies variable values:

```python
from qpiai_opt.opt_solvers.milp import WarmStart

warm_start = WarmStart(
    col_values={
        "produce": 6.0,
        "overtime": 0.0,
        "open": 1.0,
    }
)
```

Names must be non-empty strings and values must be finite real numbers. Booleans and numeric strings are rejected before Pydantic can coerce them; use `1.0`, not `True` or `"1"`. A partial value start is allowed.

A basis start must include both row and column status arrays:

```python
basis_start = WarmStart(
    col_basis_status=["basic", "lower", "upper"],
    row_basis_status=["basic", "lower"],
)
```

Allowed status strings are `lower`, `basic`, `upper`, `zero`, and `nonbasic`; input is normalized to lowercase. Providing only one basis array is invalid. A `WarmStart` with neither values nor a complete basis is also invalid.

## Blocking solve

Blocking solves read the current job state, then wait through Server-Sent Events (SSE) by default. If the stream cannot complete monitoring, the waiter falls back to polling; `poll_interval` controls that fallback interval in seconds.

```python
result = solver.solve(
    "production.lp",
    name="Production planning",
    config=config,
    warm_start=warm_start,
    experiment_name="Benchmarks",
    workspace_id="workspace-id",
    project_id="project-id",
    timeout=1800,
    request_timeout=60,
    poll_interval=5,
)
```

`solve()`:

1. validates the local path and file suffix, then reads its class and objective sense;
2. uploads the file to platform storage;
3. creates a MILP problem referencing that storage object;
4. submits the job;
5. waits for a terminal job state;
6. retrieves an inline or storage-backed result and validates it.

`timeout` is the waiting budget after submission and defaults to 600 seconds; upload, problem creation, submission, and final result retrieval are outside that budget. `request_timeout` supplies their per-request connection/read timeout and defaults to the client's timeout. Status reads use the client's timeout within the waiter's remaining budget. HTTP timeouts bound connection and read inactivity rather than total transfer duration; an in-flight request can overrun the waiting budget. `poll_interval` must be positive and finite. A local timeout stops monitoring without cancelling the platform job; resume with the same job ID. See the [timeout contract](platform-client.md#timeouts).

`workspace_id` and `project_id` are passed only to the storage upload. Experiment and compute-resource selection affect job submission.

## Nonblocking lifecycle

This solver-instance `submit()` method is explicitly platform-only. Supplying its `Client` and calling it is the caller's choice to create one potentially paid platform job.

```python
submission = solver.submit(
    "production.lp",
    name="Production planning",
    config=config,
    timeout=60,
)
```

`submit()` performs upload, problem creation, and job submission, then returns:

| Field | Meaning |
| --- | --- |
| `job_id` | Platform job identifier. |
| `status` | Normalized initial state. |
| `message` | Platform acknowledgement. |
| `problem_id` | Created platform problem identifier. |
| `storage_id` | Uploaded model-file identifier. |

It does not wait for execution. Its `timeout` is the per-request timeout used during the submission sequence.

Retrieve state and output later:

```python
status = solver.get_status(submission.job_id, timeout=60)
if status.status == "completed":
    result = status.result
else:
    result = None
```

For a completed job, `get_status()` also downloads and validates its result. For other states, `result` is `None`.

```python
result = solver.get_result(submission.job_id, timeout=60)
solution = solver.get_solution(result=result)
```

`get_result()` raises `RuntimeError` unless the job is completed. `get_solution(job_id=...)` retrieves a result first; pass an existing `result=...` to avoid another request. If a valid result has no incumbent, `get_solution()` returns `None`.

The solver retains the submitted file's problem class and objective sense for later calls made through the same solver instance. When retrieving a job from a new instance or process, supply the original file explicitly:

```python
result = MILPSolver(client=client).get_result(
    submission.job_id,
    timeout=60,
    model_path="production.lp",
)
```

`get_status(..., model_path="production.lp")` provides the same restoration. The automation API accepts the equivalent `result(client, job_id, problem="production.lp")` form. If neither the current solver instance nor the retrieval call has the source file and the platform response omits this metadata, `sense` can be `None`. `result.info().source_objective` is then also `None`; the SDK does not label a value as source-coordinate data without the source objective direction.

## Result interpretation

`solve()` and `get_result()` return `SolveResult`.

| Field | Type | Interpretation |
| --- | --- | --- |
| `job_id` | `str \| None` | Platform job identifier retained by blocking and retrieval workflows. |
| `problem_id` | `str \| None` | Platform problem identifier associated with the job. |
| `status` | `str` | Non-empty solver execution status. |
| `model_status` | `str` | Non-empty mathematical model status. Interpret this before optional solution fields. |
| `objective` | `float \| None` | Finite incumbent objective, when one exists. |
| `problem_class` | `"LP" \| "MILP"` | Identified mathematical class. |
| `execution_mode` | `"local" \| "platform"` | Execution location. |
| `sense` | `"MIN" \| "MAX" \| None` | `"MIN"` means the source objective is minimized; `"MAX"` means it is maximized. File-based solve and source-aware retrieval preserve it; otherwise it is `None`. |
| `compute_time_s`, `solver_time_s` | `float \| None` | Compute and algorithm durations in seconds, or `None` when unavailable. |
| `compute_time_ms`, `compute_time_us`, `solver_time_ms`, `solver_time_us` | `float \| None` | Runtime in milliseconds and microseconds; omitted from default serialization. |
| `num_nodes` | `int \| None` | Non-negative branch-and-bound node count, or `None` when the backend reports `-1` as unavailable. |
| `best_bound` | `float \| None` | Finite best bound in source sense and offset coordinates, when reported. With an LP source file and returned `status="optimal"`, the adapter uses the incumbent objective as its bound. Without source metadata, an `optimal` result with a nonzero incumbent, unavailable MIP nodes, zero backend bound, and no gap treats that bound as unavailable. |
| `optimality_gap` | `float \| None` | Source-coordinate relative gap, or `None` when undefined/unavailable. |
| `iteration_count` | `int` | Non-negative LP iteration count. |
| `error_message` | `str \| None` | Solver diagnostic text. |
| `solution` | `dict[str, float] \| None` | Finite variable values for a returned incumbent. |
| `dual_values` | `dict[str, float] \| None` | Finite constraint duals, if requested and available. |
| `reduced_costs` | `dict[str, float] \| None` | Finite variable reduced costs, if requested and available. |
| `basis` | basis record or `None` | Validated row and column basis statuses. |
| `ranging` | ranging record or `None` | Named variable and constraint sensitivity dictionaries; range endpoints may be infinite. |
| `solution_pool` | list of solution records or `None` | Finite objectives and finite value arrays. |
| `iis` | IIS record or `None` | Parallel row/column index, name, and bound-code arrays. |

Nested result records have these exact fields:

| Type | Fields and invariants |
| --- | --- |
| Basis record | `col_status: list[str]`, `row_status: list[str]`; each value is `lower`, `basic`, `upper`, `zero`, or `nonbasic`. |
| Solution-pool record | `objective: float`, `values: list[float]`; every number is finite. Values follow solver column order. |
| IIS record | `row_indices: list[int]`, `row_names: list[str]`, `row_bounds: list[int]`, plus the corresponding `col_indices`, `col_names`, and `col_bounds`; index/name/bound arrays have equal lengths within each axis and indices are non-negative. |
| Ranging record | `variables: dict[str, dict[str, float \| "+Inf" \| "-Inf" \| None]]`, `constraints: dict[str, dict[str, float \| "+Inf" \| "-Inf" \| None]]`; outer keys are model names. Variable measures include `cost_down`, `cost_up`, `bound_down`, and `bound_up`; constraint measures include `bound_down` and `bound_up`. Endpoints use finite numbers, the `"+Inf"`/`"-Inf"` sentinels, or `None`. |

A completed transport job is not necessarily a mathematically optimal model. Always inspect `model_status`, then test whether `solution` and `objective` are present before using them. Infeasible, unbounded, limited, and solver-error outcomes can legitimately omit an incumbent.

For code that handles several problem classes, use the normalized common view:

```python
info = result.info()
if info.has_usable_solution:
    candidate = result.solution
if info.optimality_proven:
    proven_objective = result.objective
```

`info.termination` uses the mathematical model outcome, so a platform transport `status` of `ok` with `model_status="optimal"` is reported as `optimal`. The original class-specific fields remain available and unchanged.

When a file was produced from an in-memory SDK model, validate any incumbent against that source model:

```python
if result.solution is not None:
    source_feasible = model.is_feasible(result.solution)
    source_objective = model.evaluate_objective(result.solution)
```

`source_feasible` must be true before using the incumbent. Use an application-appropriate tolerance when comparing `source_objective` with `result.objective`.

The result tables in this guide define the response schema, including basis, ranging, solution-pool, and IIS fields.

## Names, experiments, and resources

Problem names need not be unique. If `name` is omitted, the SDK creates a UTC timestamped name containing the uploaded filename.

Ordinary calls omit `compute_resource_id`; the platform automatically chooses an eligible MILP resource. Advanced callers may pin a discovered resource ID. Discover the opaque ID and place it on a submission as follows:

```python
resources = client.list_solvers(method="MILP", active_only=True)
resource = resources[0]

pinned_submission = solver.submit(
    "production.lp",
    compute_resource_id=resource.id,
)
```

Pass the same value to `MILPSolver(..., compute_resource_id=resource.id)` to make it the default for that solver instance. Per-call `experiment_id` and exact `experiment_name` override solver/client defaults. If neither an experiment ID nor an explicit name is configured, the client resolves `Default Experiment`.

## Failure and cleanup behavior

Local failures occur before chargeable submission where possible:

- a missing model path raises `FileNotFoundError`;
- an unsupported suffix raises `ValueError`;
- wrong configuration or warm-start object types raise `TypeError`;
- invalid Pydantic field values raise `pydantic.ValidationError`;
- invalid identifiers and timeouts raise `ValueError`.

Authentication, API, transport, job failure, cancellation, and wait-timeout behavior is shared with the other solver adapters.

If problem creation receives a definitive non-transient 4xx rejection, the SDK attempts to delete its uploaded storage object. If job submission receives such a rejection, it attempts to delete the created problem and then the storage object. Cleanup failures are attached to the original exception as notes.

Timeout-like status codes, server failures, and transport errors have ambiguous acceptance. The SDK retains created records and attaches reconciliation instructions to the exception instead of risking deletion of accepted work. Inspect storage, problems, and jobs before retrying.

See the shared [platform client guide](platform-client.md) and [automation failure reference](../automation.md#failure-handling). [Blocking solve](#blocking-solve) demonstrates submission of a linear model file; platform execution remains explicit.
