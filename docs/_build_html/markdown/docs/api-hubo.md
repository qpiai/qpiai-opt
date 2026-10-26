# HUBO solver API reference

Import this surface from `qpiai_opt.opt_solvers.hubo`. The [HUBO solver guide](hubo-solver.md) defines request fields, result semantics, lifecycle behavior, and failures. See the [HUBO modelling guide](hubo-modeller.md) for model construction and representation conventions.

```python
from qpiai_opt.opt_solvers.hubo import HUBOSolver
```

## Class: `HUBOSolver`

```text
HUBOSolver(api_key: 'Optional[str]' = None, url: 'Optional[str]' = None, *, client: 'Optional[Client]' = None, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, compute_resource_id: 'Optional[str]' = None) -> 'None'
```

Solve binary polynomial problems through QpiAI Platform. Supply a `Client` or an `api_key`; URL/key conflicts, identifier validation, defaults, and construction without HTTP follow the [`QUBOSolver` constructor contract](api-qubo.md#class-qubosolver). Both classes expose the same documented constructor arguments.

Public class attribute: `DEFAULT_URL = "https://opt-server.qpiai.tech/api"`.

### Methods and properties

#### Method: `submit`

```text
submit(problem: 'Union[HUBOProblem, Dict]', timeout: 'float' = 1200.0, *, name: 'Optional[str]' = None, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, compute_resource_id: 'Optional[str]' = None, **solver_params) -> 'JobSubmissionResponse'
```

Validate a `HUBOProblem` or its Python field dictionary, create one problem record, and submit one potentially paid job without waiting. Return `JobSubmissionResponse`. Timing, names, experiment precedence, resource selection, and ambiguous-outcome cleanup follow [`QUBOSolver.submit`](api-qubo.md#method-submit). A zero/constant polynomial or a register with isolated variables raises `ValueError` before HTTP because the platform cannot retain it; local execution supports these cases.

`solver_params` accepts the seven [HUBO configuration keys](hubo-solver.md#solver-configuration), plus `solver_config` as a dictionary. Merge order is stored problem configuration, explicit call-level dictionary, then direct parameter keywords. Unknown keys, explicit `solver_config=None`, null parameter values, or invalid merged values raise `ValueError`. The caller's problem is validated into a fresh request object rather than mutated by these overrides.

#### Method: `solve`

```text
solve(problem: Union[HUBOProblem, dict], *, poll_interval: float = 5.0, timeout: float = 5400.0, request_timeout: float | None = None, **solver_params) -> SolveResult
```

Submit once, wait for completion, retrieve output, and return `SolveResult`. Pass documented timing options by keyword. `solver_params` accepts all `submit()` naming/experiment/resource arguments and HUBO configuration options above. Timing values must be positive finite reals; `request_timeout=None` uses the client's timeout. See the [blocking timing contract](hubo-solver.md#blocking-solve). Failure, cancellation, and wait expiry raise `JobFailedError`, `JobCancelledError`, and `TimeoutError`; a timeout does not cancel the job.

#### Method: `get_status`

```text
get_status(job_id: 'str', timeout: 'float' = 60.0) -> 'JobStatusResponse'
```

Read one job and return `JobStatusResponse`. `job_id` follows the client's path-ID rules; `timeout` is a positive finite per-request timeout. Completed jobs include a parsed source-aware result when metadata is cached; other states contain no result. Result retrieval can involve a storage download and can raise a validation/restoration error even after completion.

#### Method: `get_result`

```text
get_result(job_id: 'str', timeout: 'float' = 60.0, *, problem: 'Optional[Union[HUBOProblem, Dict]]' = None) -> 'SolveResult'
```

Return a completed job's parsed `SolveResult`; a non-completed state raises `RuntimeError`. `timeout` is a positive finite per-request timeout. Optional `problem` accepts a `HUBOProblem` or field dictionary, takes precedence over cached source metadata, restores label order and model decoding, and enables full objective/assignment consistency checks. Without source metadata, platform labels and reported objective are retained. Malformed results, label mismatches, or inconsistent objectives raise `RuntimeError`, `ValueError`, or `pydantic.ValidationError`.

#### Method: `get_solution`

```text
get_solution(job_id: 'Optional[str]' = None, result: 'Optional[SolveResult]' = None) -> 'list[int]'
```

Supply exactly one `job_id` or HUBO `SolveResult`; neither or both raises `ValueError`, and a different result type raises `TypeError`. A job ID calls `get_result()` with its default timeout; an existing result avoids HTTP. Return its binary `list[int]` in the result's register order.

#### Method: `health`

```text
health() -> 'bool'
```

Delegate to `Client.health()`: perform an authenticated account request with a five-second timeout and return `bool`. Request/API failure returns `False`; no problem or job is created.

## Class: `HUBOProblem`

```text
HUBOProblem(*, problem_type: Literal['HUBO'] = 'HUBO', num_variables: typing.Annotated[int, Strict(strict=True), Ge(ge=0)], sense: Literal['MIN', 'MAX'] = 'MIN', vartype: Literal['BINARY'] = 'BINARY', solver_config: Optional[Dict[str, Any]] = None, variable_names: Optional[Tuple[str, ...]] = None, polynomial_dict: Dict[Tuple[str, ...], float]) -> None
```

Validated binary polynomial for local or explicit platform execution; see the [complete field contracts](hubo-solver.md#problem-fields). Tuple-key terms contain nonempty single-line string labels. Canonicalization removes repeated factors, aggregates reordered terms, drops zero totals, and checks finite coefficient scale. Without explicit `variable_names`, `num_variables` must equal the surviving label count; an explicit unique complete order can retain isolated variables locally. The count is a strict nonnegative integer. `sense`, `problem_type`, and `vartype` normalize case without stripping whitespace; only binary domains are accepted. Unknown fields and malformed values raise `pydantic.ValidationError`.

Fields: `problem_type`, `num_variables`, `sense`, `vartype`, `solver_config`, `variable_names`, `polynomial_dict`.

### Class method: `from_model`

```text
from_model(model, *, solver_config=None, **compile_options) -> HUBOProblem
```

Compile an algebraic `Model` through `Model.to_hubo_dict(**compile_options)` and retain a deep copy of its source/decoding state. Convert the compiler's minimizing polynomial back into source coordinates, preserving sense, constant, register count, and names. `solver_config` is the optional validated HUBO configuration dictionary. Later source edits do not affect the retained snapshot. The source model's compilation state is committed on success and restored on failure. A different input type raises `TypeError`; model compilation and request validation errors propagate.

## Class: `SolveResult`

```text
SolveResult(*, problem_class: Literal['HUBO'] = 'HUBO', execution_mode: Literal['local', 'platform'] = 'platform', job_id: str | None = None, problem_id: str | None = None, solver_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, compute_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, solve_info: qpiai_opt.opt_solvers.result_base.SolveInfo | None = None, num_variables: typing.Annotated[int, Strict(strict=True), Ge(ge=0)], objective: float, variable_assignment: Dict[str, Union[int, float]], solution: List[int]) -> None
```

Completed HUBO result shared by local and platform execution. `num_variables` is a strict nonnegative integer; `solution` must be a list/tuple of that length containing values equal to zero or one, normalized to Python integers. `objective` is finite and non-Boolean. `variable_assignment` maps nonempty string labels to finite non-Boolean numbers, which may be decoded nonbinary source values. These checks do not independently prove feasibility or objective consistency. Unknown fields and malformed values raise `pydantic.ValidationError`. See the [common result contract](local-solving.md#completed-result-contract) for identity, timing, serialization, and guarantees.

Fields: `problem_class`, `execution_mode`, `job_id`, `problem_id`, `solver_time_s`, `compute_time_s`, `solve_info`, `num_variables`, `objective`, `variable_assignment`, `solution`.

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

Submission record. `job_id`, `message`, and a non-null `problem_id` are stripped nonempty strings. Status is stripped/lowercased and must be one of the listed lifecycle literals. The record confirms submission rather than a mathematical solution; unknown fields or invalid values raise `pydantic.ValidationError`.

Fields: `job_id`, `status`, `message`, `problem_id`.

## Class: `JobStatusResponse`

```text
JobStatusResponse(*, job_id: str, status: Literal['pending', 'running', 'completed', 'failed', 'cancelled', 'unknown'], result: Optional[qpiai_opt.opt_solvers.hubo.models.SolveResult] = None, error: Optional[str] = None, submitted_at: Optional[str] = None, completed_at: Optional[str] = None, solver_time_s: Optional[float] = None, compute_time_s: Optional[float] = None) -> None
```

Lifecycle record. Job ID and non-null timestamp strings are stripped and nonempty; timestamps are strings rather than parsed `datetime` objects. Status is stripped/lowercased to a listed literal. A completed record must contain `SolveResult`; every other state must omit it or use `None`. `error` is optional text. Durations are optional finite nonnegative non-Boolean seconds. Unknown fields, invalid timing/text, or inconsistent result/state combinations raise `pydantic.ValidationError`.

Fields: `job_id`, `status`, `result`, `error`, `submitted_at`, `completed_at`, `solver_time_s`, `compute_time_s`.
