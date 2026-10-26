# Automation and agent workflows

Use the SDK's Python operations to prepare a model, select an execution route, verify a result, and manage platform jobs. This guide covers workflow decisions; the linked references define signatures, options, fields, and mathematical contracts.

## Before executing a task

1. Define the objective, sense, variable domains, constraints, and required output. Choose encoding resolution, penalty controls, feasibility tolerance, solution guarantee, and execution budget where needed.
2. Install with `python -m pip install qpiai-opt` and select the relevant guide below. Check the installed version with `importlib.metadata.version("qpiai-opt")`.
3. Restore canonical IR with `from_ir(problem_ir)` when necessary, then inspect the problem before execution. Keep the source model and compiled representation for verification and result retrieval.
4. Select local or platform execution. Platform jobs and paid fallback require the caller's authorization and resource budget.

Obtain a free Opt API key at [QpiAI Opt](https://opt.qpiai.tech/) for calls using `Client`, or a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/) for gate-based optimization. Load credentials from the application's secret store. Modelling, inspection, conversion, and result evaluation require no credentials; local solving verifies the appropriate key online before computation.

## Choose the right guide

| Task | Reference |
| --- | --- |
| Install or navigate the SDK | [Getting started](docs/getting-started.md), [user manual](docs/index.md) |
| Build or update a model | [Modelling workflows](docs/modelling-workflows.md), [MILP](docs/milp-modeller.md), [QP](docs/qp-modeller.md), [QUBO](docs/qubo-modeller.md), [HUBO](docs/hubo-modeller.md), [Ising](docs/ising-modeller.md) |
| Run gate-based optimization | [Gate-based optimization workflow](docs/quantum-optimization.md), [gate-based optimization API](docs/api-quantum.md) |
| Select execution and options | [Local and platform routing](docs/local-solving.md) |
| Exchange models or retain source coordinates | [Canonical IR](docs/model-ir.md), [conversions](docs/api-solvers.md#conversion-routes), [interoperability and OMMX](docs/interoperability.md) |
| Interpret results | [Result contract](docs/local-solving.md#completed-result-contract), [gate-based optimization results](docs/quantum-optimization.md#results-and-automation) |
| Manage platform jobs, resources, or credits | [Platform client](docs/platform-client.md), [credit usage and quotas](docs/platform-client.md#credit-usage-and-allocations) |
| Look up imports, schemas, or failures | [Public API index](docs/public-api.md), [solver API](docs/api-solvers.md), [schema inventory](docs/api-solvers.md#schema-inventory), [exceptions](docs/exceptions.md) |

## Inspection operations

Call `inspect(problem)` to validate the representation and obtain `problem_class`, `representation`, `metrics`, and `valid=True`; invalid inputs raise. Metrics vary by problem class. Inspection does not prove feasibility or guarantee that a problem fits local limits.

For gate-based optimization, compile an algebraic model first with `model.compile(target="QUBO", **compile_options)` or `model.compile(target="ISING", **compile_options)`, choosing its encoding options explicitly. Construct the algorithm and call `inspect(problem, algorithm=algorithm)` to check compatibility and circuit metrics. Hamiltonian inputs and higher-order models follow the [gate-based optimization input and conversion rules](docs/quantum-optimization.md#hamiltonian-conversion-and-circuit-inspection).

Use `classify(problem)` for identification, `provider_capabilities()` for execution capabilities, and `LOCAL_LIMITS` for local capacity. These operations do not solve or submit work. See the [inspection API](docs/api-solvers.md#function-inspect) and [local limits](docs/local-solving.md#structural-limits).

## Deterministic decision procedure

Import operations from `qpiai_opt.opt_solvers`, modelling and IR functions from `qpiai_opt.opt_modeller`, and gate-based optimization algorithms and configuration from `qpiai_opt.quantum`.

For gate-based optimization, authenticate with `QpiAIQuantumAuth.login(api_key=QCLOUD_API_KEY)` and construct `execution = QuantumRun()`, setting execution options as needed. For calls without `algorithm`, construct `client = Client(api_key=OPT_API_KEY)`. Assign the result of the selected call below to `solve_result`.

| Route | Call | Execution scope |
| --- | --- | --- |
| Local gate-based optimization | `solve(problem, algorithm=algorithm, execution=execution)` | Authenticated QpiAI local quantum simulator. |
| Gate-based optimization on QCloud | `solve(problem, mode="platform", algorithm=algorithm, execution=execution)` | QCloud circuit execution; can consume credits. |
| Local | `solve(problem, client=client)` | Default route; free authenticated local computation. |
| Platform | `solve(problem, mode="platform", client=client)` | Explicit platform job; can consume credits. |
| Local-first | `solve(problem, mode="auto", client=client)` | Local only unless `allow_platform=True` also authorizes fallback. |

Gate-based optimization rejects `mode="auto"`, `allow_platform=True`, and the Opt `Client`. Configure QCloud device selection through `QuantumRun`; use the [gate-based optimization guide](docs/quantum-optimization.md) for supported settings.

QP is local-only. Automatic fallback occurs only after `LocalLimitExceeded`. Authentication, validation, transport, and solver failures do not trigger fallback. Omit options whose value is `None`; options for the wrong class raise. Use the [routing modes and option reference](docs/local-solving.md#modes) before selecting fallback or class-specific controls.

## Blocking solve

Read `solve_result.info()` before accepting an optimization candidate. Check `has_usable_solution`, `solution_feasible`, and `optimality_proven` against the application's required guarantee; a heuristic candidate is not an optimality proof.

When a usable candidate and source `Model` are available, call `feasibility(model, solve_result.variable_assignment)` for QUBO/HUBO/Ising, or `feasibility(model, solve_result.solution)` for LP/MILP/QP. When no usable candidate exists, report the termination and guarantee and skip assignment evaluation. Generic Hamiltonian results provide `eigenvalue` without a variable assignment.

Serialize results with `model_dump(mode="json")`, retaining `solve_info`. Restore them with the corresponding result class's `model_validate()` method. Validate stored `solve_info` with `QuantumSolveInfo` for `QuantumResult`, or `SolveInfo` for other result types. Full fields, guarantees, timings, and serialization are defined in the [result contract](docs/local-solving.md#completed-result-contract) and [gate-based optimization result reference](docs/api-quantum.md#class-quantumresult).

## Nonblocking platform lifecycle

Use the optimization job lifecycle for platform calls without `algorithm`. Gate-based optimization algorithms use the blocking workflow above.

| Operation | Action |
| --- | --- |
| `submit(problem, client=client, **options)` | Submit once; preserve the returned identifiers and original problem. |
| `status(client, job_id)` | Read the existing job's state without resubmitting. |
| `result(client, job_id, problem=original_problem)` | Retrieve a completed job as a typed result with source restoration. |
| `cancel(client, job_id)` | Request cancellation of a job owned by the workflow; check its state afterward. |

Retain the original problem and decoding metadata until retrieval. Omitting `problem` from `result()` returns the raw extracted payload. After an ambiguous submission failure, reconcile existing jobs before retrying. See the [submission](docs/api-solvers.md#function-submit), [status](docs/api-solvers.md#function-status), [retrieval](docs/api-solvers.md#function-result), and [cancellation](docs/api-solvers.md#function-cancel) contracts, including exact return fields and failure conditions.

## Conversion and verification

Use `convert(problem, target, **options)` for an explicit representation change. The [conversion reference](docs/api-solvers.md#conversion-routes) lists every supported source/target pair, return type, option, and mathematical restriction. Retain conversion records and source metadata for decoding and source-objective interpretation.

`feasibility(model, values, tolerance=1e-6)` requires a complete source-name assignment and returns `feasible`, `objective`, `violations`, and `tolerance`. It also accepts the model's deterministic auxiliary columns. Use its [input and return contract](docs/api-solvers.md#function-feasibility) for domain diagnostics, tolerance rules, and rejected assignments.

## Schemas and cross-process requests

Use `schemas()` to obtain JSON schemas for request envelopes, problems, configurations, results, and platform records:

```python
from qpiai_opt.opt_solvers import schemas

schema_map = schemas()
request_schema = schema_map["solve_request"]
result_schema = schema_map["solve_result"]
```

The [schema inventory](docs/api-solvers.md#schema-inventory) defines the available keys. Request envelopes reject unknown outer fields. Validate the selected request schema before dispatch. Use `from_ir()` to restore `problem_ir` for inspection, solve, or submission requests, or `model_ir` for feasibility requests. Pass feasibility `values` and its optional `tolerance`; job requests carry `job_id`. Supply an Opt `Client` separately for `solve()` calls without `algorithm` and for job operations. Inspection and feasibility need no client. Credentials and client/session state stay outside IR, request data, results, and logs.

`solve_request` does not include `algorithm` or `execution`. A gate-based optimization integration selects and constructs its algorithm through the Python API, validates settings with `QuantumRun`, and supplies QCloud authentication separately; algorithm instances, callbacks, and circuits are not serialized by these request schemas.

## Failure handling

| Failure | Workflow action |
| --- | --- |
| Invalid types, domains, coefficients, or options | Correct the input using the relevant API contract; rescale values outside supported numerical ranges. |
| `LocalLimitExceeded` | Reduce the problem or select an authorized execution route. |
| `CapabilityError` | Read `compatible_alternatives` and select a supported representation or route within the execution policy. |
| `AuthenticationError` | Correct the credential or connectivity; do not switch execution routes. |
| Ambiguous transport or submission failure | Reconcile the existing job before retrying; avoid duplicate submissions. |
| Failed or cancelled job | Retain its identifiers and error; treat it as having no completed solution. |

Use the [exception reference](docs/exceptions.md), [structured local failures](docs/local-solving.md#structured-failures), and [platform retry policy](docs/platform-client.md#failure-and-retry-policy) for specific recovery rules.
