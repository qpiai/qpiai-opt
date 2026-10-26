# MILP solver API reference

Import this surface from `qpiai_opt.opt_solvers.milp`. The [MILP solver guide](milp-solver.md) defines request fields, result semantics, lifecycle behavior, and failures. See the [MILP modelling guide](milp-modeller.md) for model construction and representation conventions.

```python
from qpiai_opt.opt_solvers.milp import MILPSolver
```

## Class: `MILPSolver`

```text
MILPSolver(api_key: 'Optional[str]' = None, url: 'Optional[str]' = None, *, client: 'Optional[Client]' = None, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, compute_resource_id: 'Optional[str]' = None) -> 'None'
```

Solve LP/MPS models through QpiAI Platform. Supply a `Client` or an `api_key`; URL/key conflicts, identifier validation, defaults, and construction without HTTP follow the [`QUBOSolver` constructor contract](api-qubo.md#class-qubosolver). Both classes expose the same documented constructor arguments.

Public class attributes: `DEFAULT_URL = "https://opt-server.qpiai.tech/api"` and `SUPPORTED_SUFFIXES = (".lp", ".mps", ".lp.gz", ".mps.gz")`.

### Methods and properties

#### Method: `submit`

```text
submit(model_path: 'str | MILPProblem', *, config: 'Optional[MILPSolverConfig]' = None, warm_start: 'Optional[WarmStart]' = None, timeout: 'float' = 1200.0, name: 'Optional[str]' = None, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, compute_resource_id: 'Optional[str]' = None, workspace_id: 'Optional[str]' = None, project_id: 'Optional[str]' = None) -> 'JobSubmissionResponse'
```

Upload one model file, create a problem record, and submit one potentially paid job without waiting. Return `JobSubmissionResponse`. `model_path` accepts an existing LP/MPS/plain-gzip path or `MILPProblem`; suffix matching is case-insensitive. An in-memory problem exports an independent source snapshot to a temporary LP file for upload, then removes that local file. A path-backed problem uses its referenced file.

`config` must be `MILPSolverConfig` or `None`; a supplied value overrides a problem's stored configuration. If neither is present, platform defaults use a new `MILPSolverConfig()`. `warm_start` must be `WarmStart` or `None`. `timeout` is a positive finite per-request connection/read timeout. `name=None` generates a UTC-labelled problem name containing the filename. Experiment ID/name and compute-resource ID override solver/client defaults. `workspace_id` and `project_id` scope storage upload only. IDs follow the client's path rules.

Missing paths raise `FileNotFoundError`, unsupported suffixes and invalid IDs/timing raise `ValueError`, wrong config/start object types raise `TypeError`, and malformed models/options raise their validation errors. A storage response lacking a truthy `id` or `ID` raises `RuntimeError`. HTTP failures propagate. Definitive non-transient 4xx rejections trigger best-effort cleanup in dependency order; ambiguous outcomes retain records for reconciliation. See [cleanup behavior](milp-solver.md#failure-and-cleanup-behavior).

#### Method: `solve`

```text
solve(model_path: 'str | MILPProblem', *, config: 'Optional[MILPSolverConfig]' = None, warm_start: 'Optional[WarmStart]' = None, timeout: 'float' = 600.0, poll_interval: 'float' = 5.0, request_timeout: 'Optional[float]' = None, name: 'Optional[str]' = None, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, compute_resource_id: 'Optional[str]' = None, workspace_id: 'Optional[str]' = None, project_id: 'Optional[str]' = None) -> 'SolveResult'
```

Perform `submit()` once, wait for a completed job, retrieve output, and return `SolveResult`. Submission options have the contracts above. `timeout` and `poll_interval` are positive finite values; the waiting budget begins after submission and defaults to 600 seconds. `request_timeout=None` uses the client's per-request timeout. See [blocking timing](milp-solver.md#blocking-solve) for operations outside the wait budget and in-flight overruns. Failed, cancelled, and expired waits raise `JobFailedError`, `JobCancelledError`, and `TimeoutError`. A timeout leaves the job running.

#### Method: `get_status`

```text
get_status(job_id: 'str', timeout: 'float' = 60.0, *, model_path: 'Optional[str]' = None, problem: 'Optional[MILPProblem]' = None) -> 'JobStatusResponse'
```

Read one job and return `JobStatusResponse`. `job_id` follows path-ID rules and `timeout` is a positive finite per-request timeout. Completed jobs include a parsed result, possibly downloaded from storage; other states have `result=None`. Optional `model_path` reads the original file's class, sense, and naming metadata; optional `problem` supplies a `MILPProblem` and its retained source model or path. Supply at most one: both raises `ValueError`, and another problem type raises `TypeError`. Explicit source context takes precedence over cached metadata. Invalid source paths or malformed completed output can raise before a response is returned.

#### Method: `get_result`

```text
get_result(job_id: 'str', timeout: 'float' = 60.0, *, model_path: 'Optional[str]' = None, problem: 'Optional[MILPProblem]' = None) -> 'SolveResult'
```

Return a completed job's parsed `SolveResult`; a non-completed state raises `RuntimeError`. Timing and optional `model_path`/`problem` follow `get_status()`. Supply the original source context when retrieving in another process. Source-file class or sense conflicting with returned metadata raises `RuntimeError`. A retained source model restores names, recomputes its objective, and checks source feasibility; absent source context cannot recover omitted model semantics. Parsing and validation failures propagate.

#### Method: `get_solution`

```text
get_solution(job_id: 'Optional[str]' = None, result: 'Optional[SolveResult]' = None) -> 'Optional[dict[str, float]]'
```

Supply exactly one `job_id` or MILP `SolveResult`; neither or both raises `ValueError`, and a different result type raises `TypeError`. A job ID calls `get_result()` with its default timeout; an existing result avoids HTTP. Return its `dict[str,float]` solution or `None` when no incumbent is available.

#### Method: `health`

```text
health() -> 'bool'
```

Delegate to `Client.health()`: perform an authenticated account request with a five-second timeout and return `bool`. Request/API failure returns `False`; no problem or job is created.

## Class: `MILPSolverConfig`

```text
MILPSolverConfig(*, time_limit: typing.Annotated[float, Ge(ge=0)] = 300.0, mip_max_nodes: typing.Annotated[int, Strict(strict=True), Ge(ge=-1)] = -1, solution_limit: typing.Annotated[int, Strict(strict=True), Ge(ge=-1)] = -1, objective_bound: float | None = None, objective_target: float | None = None, mip_rel_gap: typing.Annotated[float, Ge(ge=0)] = 0.0001, mip_abs_gap: typing.Annotated[float, Ge(ge=0)] = 1e-06, primal_feasibility_tolerance: typing.Annotated[float, Gt(gt=0)] = 1e-07, dual_feasibility_tolerance: typing.Annotated[float, Gt(gt=0)] = 1e-07, mip_feasibility_tolerance: typing.Annotated[float, Gt(gt=0)] = 1e-06, threads: typing.Annotated[int, Strict(strict=True), Ge(ge=0)] = 0, presolve: Literal['on', 'off', 'choose'] = 'choose', solver_algorithm: Literal['simplex', 'ipm', 'choose'] = 'choose', mip_heuristic_effort: typing.Annotated[float, Ge(ge=0), Le(le=1)] = 0.05, output_flag: typing.Annotated[bool, Strict(strict=True)] = False, log_file: str | None = None, include_dual_info: typing.Annotated[bool, Strict(strict=True)] = False, compute_ranging: typing.Annotated[bool, Strict(strict=True)] = False, compute_iis: typing.Annotated[bool, Strict(strict=True)] = True, collect_solution_pool: typing.Annotated[bool, Strict(strict=True)] = False) -> None
```

Platform LP/MILP configuration; the [configuration tables](milp-solver.md#solver-configuration) define each field's purpose, default, range, and requested diagnostics. Unknown fields are forbidden. Numeric values must be finite and non-Boolean; strict integer and Boolean fields reject coercion. `solution_limit` accepts `-1` or positive integers, not zero. Algorithm enum values normalize case without whitespace stripping. `log_file` is `None` or a non-whitespace string, retained as supplied and interpreted on the solver host. Invalid values raise `pydantic.ValidationError`.

Fields: `time_limit`, `mip_max_nodes`, `solution_limit`, `objective_bound`, `objective_target`, `mip_rel_gap`, `mip_abs_gap`, `primal_feasibility_tolerance`, `dual_feasibility_tolerance`, `mip_feasibility_tolerance`, `threads`, `presolve`, `solver_algorithm`, `mip_heuristic_effort`, `output_flag`, `log_file`, `include_dual_info`, `compute_ranging`, `compute_iis`, `collect_solution_pool`.

## Class: `WarmStart`

```text
WarmStart(*, col_values: dict[str, float] | None = None, col_basis_status: list[str] | None = None, row_basis_status: list[str] | None = None) -> None
```

Initial platform solution, basis, or both. `col_values` maps nonempty variable names to finite non-Boolean real values; omitted variables form a partial start. Both basis arrays must be supplied together and contain case-insensitive `lower`, `basic`, `upper`, `zero`, or `nonbasic` strings, normalized to lowercase without stripping. At least values or a complete basis is required; empty supplied arrays/dictionaries are accepted by this schema. The SDK validates shape and values but does not establish start feasibility, basis dimensions, or agreement with a particular model. Unknown fields or malformed data raise `pydantic.ValidationError`.

Fields: `col_values`, `col_basis_status`, `row_basis_status`.

## Class: `SolveResult`

```text
SolveResult(*, problem_class: Literal['LP', 'MILP'] = 'MILP', execution_mode: Literal['local', 'platform'] = 'platform', job_id: str | None = None, problem_id: str | None = None, solver_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, compute_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, solve_info: qpiai_opt.opt_solvers.result_base.SolveInfo | None = None, sense: Optional[Literal['MIN', 'MAX']] = None, status: str, model_status: str, objective: float | None = None, num_nodes: typing.Annotated[int | None, Strict(strict=True), Ge(ge=0)] = 0, best_bound: float | None = None, optimality_gap: float | None = None, iteration_count: typing.Annotated[int, Strict(strict=True), Ge(ge=0)] = 0, error_message: str | None = None, solution: dict[str, float] | None = None, dual_values: dict[str, float] | None = None, reduced_costs: dict[str, float] | None = None, basis: qpiai_opt.opt_solvers.milp.models.BasisData | None = None, ranging: qpiai_opt.opt_solvers.milp.models.RangingData | None = None, solution_pool: list[qpiai_opt.opt_solvers.milp.models.PoolSolution] | None = None, iis: qpiai_opt.opt_solvers.milp.models.IISData | None = None) -> None
```

Completed LP/MILP result for local or platform execution. The [result tables](milp-solver.md#result-interpretation) define every field, optional nested record, and mathematical interpretation. `status` is stripped/lowercased; `model_status` is stripped without changing case. Numeric objectives, bounds, gaps, and assignments must be finite and non-Boolean; gaps are nonnegative. Node/iteration counts are strict nonnegative integers, with `num_nodes=None` for unavailable data; the exact Python integer `-1` is also normalized to `None` at construction. Unknown fields and malformed data raise `pydantic.ValidationError`.

When objective and bound are both present, they match if `abs(objective-best_bound) <= 1e-10*max(1, abs(objective), abs(best_bound))`. Otherwise a MIN bound cannot exceed the incumbent and a MAX bound cannot fall below it. An `optimal` status requires a matching bound when one is supplied. If a gap is supplied too, it must agree with zero for matching values or `abs(objective-best_bound)/abs(objective)` otherwise, using relative tolerance `1e-10` and absolute tolerance `1e-12`; a zero incumbent with a different bound has no accepted finite gap. These consistency checks do not themselves establish primal feasibility or an optimality proof.

Fields: `problem_class`, `execution_mode`, `job_id`, `problem_id`, `solver_time_s`, `compute_time_s`, `solve_info`, `sense`, `status`, `model_status`, `objective`, `num_nodes`, `best_bound`, `optimality_gap`, `iteration_count`, `error_message`, `solution`, `dual_values`, `reduced_costs`, `basis`, `ranging`, `solution_pool`, `iis`.

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
JobSubmissionResponse(*, job_id: str, status: Literal['pending', 'running', 'completed', 'failed', 'cancelled', 'unknown'], message: str, problem_id: str, storage_id: str) -> None
```

Submission record. All five fields are required stripped nonempty strings; status additionally normalizes to a listed lowercase lifecycle literal. Unknown fields or invalid values raise `pydantic.ValidationError`. The record confirms submission rather than a mathematical solution.

Fields: `job_id`, `status`, `message`, `problem_id`, `storage_id`.

## Class: `JobStatusResponse`

```text
JobStatusResponse(*, job_id: str, status: Literal['pending', 'running', 'completed', 'failed', 'cancelled', 'unknown'], result: qpiai_opt.opt_solvers.milp.models.SolveResult | None = None, error: str | None = None, submitted_at: str | None = None, completed_at: str | None = None, compute_time_s: float | None = None, solver_time_s: float | None = None) -> None
```

Lifecycle record. Job ID and non-null timestamp strings are stripped and nonempty; timestamps remain strings rather than parsed `datetime` objects. Status normalizes to a listed lowercase literal. Completed state requires `SolveResult`; other states require `result=None`. Durations are optional finite nonnegative non-Boolean seconds, and `error` is optional text. Unknown fields, invalid values, or an inconsistent state/result combination raise `pydantic.ValidationError`.

Fields: `job_id`, `status`, `result`, `error`, `submitted_at`, `completed_at`, `compute_time_s`, `solver_time_s`.

## Class: `MILPProblem`

Import from `qpiai_opt.opt_solvers.milp`.

```text
MILPProblem(*, problem_type='MILP', config=None, model_path=None, model=None)
```

Provide exactly one of `model` or `model_path`. `model` accepts a `Model` or its canonical model-IR dictionary, validates its linear structure and matrix, and retains a deep copy. `model_path` accepts `str` or `Path` ending in `.lp`, `.mps`, `.lp.gz`, or `.mps.gz`, case-insensitively; the file is read to determine its class. A file path retains a reference rather than copying file contents. Omitted `problem_type` is inferred; an explicit case-insensitive LP/MILP marker must match that class. `config` is `MILPSolverConfig`, a validated field dictionary, or `None`.

```text
MILPProblem.from_model(model, *, config=None) -> MILPProblem
```

This class method delegates to model-backed construction with the same snapshot and validation rules. The retained `.model` is mutable; preserve its state and referenced file contents while interpreting submitted jobs. `solve()` and `MILPSolver` accept the container directly; retrieval accepts `problem=...` to restore source context in another process.

Local mode permits only explicitly supplied `time_limit`, `mip_max_nodes`, and `mip_rel_gap` configuration fields, mapped to local options; unsupported explicit fields raise `ValueError`. Local `time_limit` must be positive, and a supplied nondefault node limit must be positive; gap/node settings apply only to MILP. Direct solve options override stored local defaults. Container serialization emits model IR and only explicitly set config fields; `to_ir(problem)` instead emits the mathematical model alone, omitting execution configuration and returning a `Model` on load.

Fields: `problem_type`, `config`, `model_path`, `model`.

## Nested result records

These types appear in `SolveResult` fields and JSON schemas; their definitions are in `qpiai_opt.opt_solvers.milp.models`. Their constructors use keyword fields and reject unknown fields.

| Type | Required constructor fields | Validation |
| --- | --- | --- |
| `BasisData` | `col_status: list[str]`, `row_status: list[str]` | Lists/tuples of the five basis status strings above, normalized to lowercase; dimensions are not checked against a model. |
| `PoolSolution` | `objective: float`, `values: list[float]` | Finite non-Boolean numbers; value lists/tuples are normalized to floats without checking model dimensions or feasibility. |
| `IISData` | `row_indices`, `row_names`, `row_bounds`, `col_indices`, `col_names`, `col_bounds` | Lists/tuples; indices are nonnegative non-Boolean integers, names are nonempty strings, bounds are integer codes. Index/name/bound lengths agree within each axis. |
| `RangingData` | `variables`, `constraints` | Dictionaries keyed by nonempty names, each containing nonempty measure-name keys and finite non-Boolean floats, exact `"+Inf"`/`"-Inf"` strings, or `None`. Measure keys are not limited to a fixed enum. |

Nested record validation raises `pydantic.ValidationError`; empty collections are valid when their parallel lengths agree. The [result guide](milp-solver.md#result-interpretation) explains column order and sensitivity measures.
