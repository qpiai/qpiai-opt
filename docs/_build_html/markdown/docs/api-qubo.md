# QUBO solver API reference

Import this surface from `qpiai_opt.opt_solvers.qubo`. The [QUBO solver guide](qubo-solver.md) defines request fields, result semantics, lifecycle behavior, and failures. See the [QUBO modelling guide](qubo-modeller.md) for model construction and representation conventions.

```python
from qpiai_opt.opt_solvers.qubo import QUBOSolver
```

## Class: `QUBOSolver`

```text
QUBOSolver(api_key: 'Optional[str]' = None, url: 'Optional[str]' = None, *, client: 'Optional[Client]' = None, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, compute_resource_id: 'Optional[str]' = None) -> 'None'
```

Solve QUBO problems through QpiAI Platform. Supply a `Client` or an `api_key` from your application's secret store; construction performs no HTTP request. `url` is the platform API root when constructing a client. When a client is supplied, an additional key or URL must match it or construction raises `ValueError`; another client type raises `TypeError`. Experiment ID/name and compute-resource ID establish solver defaults; call-level selections take precedence. Identifier and URL validation follows [`Client`](platform-client.md#construction).

Public class attribute: `DEFAULT_URL = "https://opt-server.qpiai.tech/api"`.

### Methods and properties

#### Method: `submit`

```text
submit(problem: 'Union[QUBOProblem, Dict]', timeout: 'float' = 1200.0, *, name: 'Optional[str]' = None, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, compute_resource_id: 'Optional[str]' = None) -> 'JobSubmissionResponse'
```

Validate a `QUBOProblem` or its Python field dictionary, create one problem record, and submit one potentially paid job without waiting. `timeout` is a positive finite per-request connection/read timeout, defaulting to 1200 seconds. `name=None` generates a UTC-labelled problem name; experiment ID/name override solver/client defaults and a supplied ID takes precedence. `compute_resource_id=None` uses the solver default or automatic platform selection. Return `JobSubmissionResponse` with normalized status and identifiers.

An empty normalized QUBO raises `ValueError` before HTTP. Invalid request fields raise `pydantic.ValidationError`; invalid identifiers/timing raise `ValueError`. Transport and platform failures propagate. A definitive non-transient 4xx submission rejection triggers best-effort deletion of the newly created problem. Ambiguous outcomes retain it for reconciliation; see [submission failures](qubo-solver.md#submission-failures).

#### Method: `solve`

```text
solve(problem: Union[QUBOProblem, dict], *, poll_interval: float = 5.0, timeout: float = 5400.0, request_timeout: float | None = None, **submit_options) -> SolveResult
```

Submit once, wait for completion, retrieve output, and return a source-aware `SolveResult`. Pass the documented timing options by keyword. `submit_options` accepts `name`, `experiment_id`, `experiment_name`, and `compute_resource_id`; unknown arguments raise `TypeError`. `poll_interval` and `timeout` must be positive finite values; `request_timeout=None` uses the client's timeout. The [blocking timing contract](qubo-solver.md#blocking-solve) distinguishes the waiting budget from request timeouts. Failure, cancellation, and wait expiry raise `JobFailedError`, `JobCancelledError`, and `TimeoutError`; a timeout does not cancel the job.

#### Method: `get_status`

```text
get_status(job_id: 'str', timeout: 'float' = 60.0) -> 'JobStatusResponse'
```

Return `JobStatusResponse` after reading one job. `job_id` follows the client's path-ID rules; `timeout` is a positive finite per-request timeout. Completed jobs include a parsed result and may require a storage download; other states contain no result. Cached source metadata is used when present. Result validation/restoration can fail even after the job reaches `completed`.

#### Method: `get_result`

```text
get_result(job_id: 'str', timeout: 'float' = 60.0, *, problem: 'Optional[Union[QUBOProblem, Dict]]' = None) -> 'SolveResult'
```

Read the job and return its parsed `SolveResult`; a state other than completed raises `RuntimeError`. `timeout` is a positive finite per-request timeout. Optional `problem` accepts a `QUBOProblem` or field dictionary and takes precedence over solver-instance metadata, restoring source sense, offset, names, and model decoding. Pass the original problem when retrieving from another process; without it or cached metadata, the reported minimizing matrix objective cannot be restored to source coordinates. A mismatched source problem or malformed service result can raise `RuntimeError`, `ValueError`, or `pydantic.ValidationError`.

#### Method: `get_solution`

```text
get_solution(job_id: 'Optional[str]' = None, result: 'Optional[SolveResult]' = None) -> 'List[int]'
```

Supply exactly one `job_id` or QUBO `SolveResult`; neither or both raises `ValueError`, and a different result type raises `TypeError`. A job ID calls `get_result()` with its default timeout; an existing result avoids HTTP. Return its `list[int]` solution in QUBO index order.

#### Method: `health`

```text
health() -> 'bool'
```

Delegate to `Client.health()`: perform an authenticated account request with a five-second timeout and return `bool`. Request/API failure returns `False`; no problem or job is created.

## Class: `QUBOProblem`

```text
QUBOProblem(*, problem_type: Literal['QUBO'] = 'QUBO', num_variables: typing.Annotated[int, Strict(strict=True), Ge(ge=0)], matrix_format: Literal['symmetric', 'upper'] = 'symmetric', max_evaluations: typing.Annotated[int, Strict(strict=True), Gt(gt=0)] = 10000, sense: Literal['MIN', 'MAX'] = 'MIN', offset: float = 0.0, variable_names: Optional[Tuple[str, ...]] = None, qubo_dict: Dict[Tuple[int, int], float]) -> None
```

Validated QUBO problem for local or explicit platform execution. Its [field contracts](qubo-solver.md#quboproblem-fields) define count, coefficients, names, offset, direction, and platform evaluation budget. Unknown fields are forbidden. Boolean coefficients, offsets, counts, and indices are rejected. Upper input is converted immediately to symmetric half-coefficient storage; malformed mirrors, indices, names, or coefficient scale raise `pydantic.ValidationError`. Sense and problem marker are case-insensitive without whitespace stripping; matrix formats also strip whitespace and accept the documented aliases.

Fields: `problem_type`, `num_variables`, `matrix_format`, `max_evaluations`, `sense`, `offset`, `variable_names`, `qubo_dict`.

### Methods and properties

#### Method: `from_model`

```text
from_model(model, *, max_evaluations=10000, **compile_options)
```

Return `QUBOProblem` by compiling an algebraic `Model` and deep-copying its source/decoding state. The compiler's minimizing representation is converted into source coordinates, including MAX sense and offset. `max_evaluations` is the platform evaluation budget; `compile_options` accepts the public QUBO compilation controls except `matrix_format`, which this factory fixes to symmetric storage. The source model's compiled state is committed on success and restored on failure. Later model edits do not change this problem's snapshot. A different input type raises `TypeError`; compilation and request validation errors propagate.

## Class: `SolveResult`

```text
SolveResult(*, problem_class: Literal['QUBO'] = 'QUBO', execution_mode: Literal['local', 'platform'] = 'platform', job_id: str | None = None, problem_id: str | None = None, solver_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, compute_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, solve_info: qpiai_opt.opt_solvers.result_base.SolveInfo | None = None, num_nodes: typing.Annotated[int, Strict(strict=True), Ge(ge=0)], objective: float, variable_assignment: Dict[str, Union[int, float]], solution: List[int]) -> None
```

Completed QUBO result shared by local and platform execution. `num_nodes` is a strict nonnegative integer and `solution` must be a list/tuple of that length containing values equal to zero or one; construction normalizes bits to Python integers. `objective` is finite and non-Boolean. `variable_assignment` maps nonempty string names to finite non-Boolean numeric values, which may be decoded nonbinary source values. These validations do not independently prove feasibility or objective consistency. Unknown fields are forbidden; malformed data raise `pydantic.ValidationError`. See the [common result contract](local-solving.md#completed-result-contract) for identity, timing, serialization, and guarantee semantics.

Fields: `problem_class`, `execution_mode`, `job_id`, `problem_id`, `solver_time_s`, `compute_time_s`, `solve_info`, `num_nodes`, `objective`, `variable_assignment`, `solution`.

### Methods and properties

#### Method: `runtime`

```text
runtime(unit: 'str' = 's', *, kind: 'str' = 'compute') -> 'float | None'
```

Return the compute or solver duration in seconds, milliseconds, or microseconds. `kind="compute"` is the default; unknown durations return `None`.

#### Method: `info`

```text
info() -> 'SolveInfo'
```

Return the common provider, method, termination, feasibility, proof, guarantee, source-objective, transformation, and settings view.

## Class: `JobSubmissionResponse`

```text
JobSubmissionResponse(*, job_id: str, status: Literal['pending', 'running', 'completed', 'failed', 'cancelled', 'unknown'], message: str, problem_id: Optional[str] = None) -> None
```

Submission record. `job_id`, `message`, and a non-null `problem_id` are stripped nonempty strings. Status is stripped/lowercased and must be one of the six listed lifecycle literals. The record confirms submission rather than a mathematical solution; unknown fields or invalid values raise `pydantic.ValidationError`.

Fields: `job_id`, `status`, `message`, `problem_id`.

## Class: `JobStatusResponse`

```text
JobStatusResponse(*, job_id: str, status: Literal['pending', 'running', 'completed', 'failed', 'cancelled', 'unknown'], result: Optional[qpiai_opt.opt_solvers.qubo.models.SolveResult] = None, error: Optional[str] = None, submitted_at: Optional[str] = None, completed_at: Optional[str] = None, compute_time_s: Optional[float] = None, solver_time_s: Optional[float] = None) -> None
```

Lifecycle record. Job ID and non-null timestamp strings are stripped and nonempty; timestamps are strings rather than parsed `datetime` objects. Status is stripped/lowercased to a listed literal. A completed record must contain `SolveResult`; every other state must omit it or use `None`. `error` is optional text. Durations are optional finite nonnegative non-Boolean seconds. Unknown fields, invalid timing/text, or inconsistent result/state combinations raise `pydantic.ValidationError`.

Fields: `job_id`, `status`, `result`, `error`, `submitted_at`, `completed_at`, `compute_time_s`, `solver_time_s`.
