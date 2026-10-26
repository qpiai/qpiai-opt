# Local solving and automatic routing

`qpiai_opt.opt_solvers.solve()` is the common blocking entry point. This chapter covers calls without `algorithm`: the SDK detects the problem class, normalizes the representation, chooses an execution mode, and returns `SolveResult`. Calls with `algorithm` follow the [gate-based optimization workflow](quantum-optimization.md) and return `QuantumResult`.

```text
solve(
    problem,
    *,
    mode="local",
    client=None,
    allow_platform=False,
    **options,
) -> SolveResult
```

## Accepted problem inputs

| Input | Detected class | Requirements |
|---|---|---|
| `Model` | `LP`, `MILP`, `QP`, `QUBO`, `HUBO`, or `ISING` | `Model.validate()` must accept its structure; `MIP` is normalized to `MILP` |
| `QUBOProblem` | `QUBO` | validated indices, finite coefficients, declared matrix format |
| `HUBOProblem` | `HUBO` | tuple-key polynomial with finite coefficients; empty and constant polynomials are accepted locally |
| `IsingProblem` | `ISING` | indexed linear/quadratic fields and finite offset |
| `.lp`, `.mps`, `.lp.gz`, `.mps.gz` path | `LP` or `MILP` | existing readable file; file integrality determines the class; Unicode paths are accepted |
| linear dictionary | `LP` or `MILP` | either the `to_lp_matrix()` keys or `c`, `A`, `b_l`, `b_u`; optional bounds, integrality, names, sense, offset |
| `{(i, j): coefficient}` | `QUBO` | tuple keys of degree two; interpreted as upper/direct polynomial coefficients |
| `{term_tuple: coefficient}` | `HUBO` | at least one term with degree above two |
| marked dictionary | declared class | `problem_type` or `method` equal to `LP`, `MILP`, `MIP`, `QUBO`, `HUBO`, or `ISING` |

An empty raw QUBO dictionary cannot imply a variable count. Use `QUBOProblem(num_variables=..., qubo_dict={})` instead. Ambiguous or unsupported objects raise `TypeError`; malformed recognized inputs raise `ValueError`, a modeller exception, or `pydantic.ValidationError`.

Linear and Ising dictionaries reject unknown fields. Linear arrays require actual finite numeric values rather than booleans or numeric strings; integrality codes are the integers `0`, `1`, `2`, or `3`. If both `problem_type` and `method` are present they must agree, and an LP/MILP marker must agree with the supplied integrality vector. `inspect()` and `solve()` apply the same structural checks.

Duplicate coordinates in a supplied SciPy sparse constraint matrix represent one mathematical coefficient and are combined independently of storage order. Malformed or non-finite aggregates are rejected before solver execution.

## Modes

| `mode` | Additional arguments | Payment behavior |
|---|---|---|
| `"local"` | `client=Client(...)` | free local computation; online Opt authentication only |
| `"platform"` | `client=Client(...)` | explicitly creates platform work and can consume credits |
| `"auto"` | `client=Client(...)` | authenticated local attempt; propagates local-limit failure |
| `"auto"` | `allow_platform=True`, `client=Client(...)` | tries local; only a `LocalLimitExceeded` triggers paid fallback |

`allow_platform` must be a Boolean. `auto` does not fall back after validation, programming, infeasibility, transport, or solver failures.

Registration and local solving are free. Register for an Opt key at [QpiAI Opt](https://opt.qpiai.tech/), load it from your application's secret manager, and initialize `client = Client(api_key=OPT_API_KEY)`. The compiled runtime verifies the key with the Opt service before each solve. Missing, rejected, expired, revoked, rate-limited, or unreachable authentication raises `AuthenticationError` before computation. The local verifier uses the production authentication endpoint independently of the platform client's `base_url` and HTTP session.

Direct provider use follows the same rule: `LocalProvider(client=client).solve(problem, problem_class="QUBO")`. Installing without a compatible runtime does not enable a Python fallback; local execution raises an installation error. Modelling, classification, inspection, validation, conversion, and mathematical result checks do not authenticate.

Platform accepts a `Model` or LP/MPS path for `LP`/`MILP`; its API does not accept the local matrix dictionary. Attempting that combination raises `CapabilityError` with `reason` and `compatible_alternatives`.

## Local solver

The base installation solves linear, mixed-integer linear, convex continuous quadratic, QUBO, HUBO, and Ising problems. LP and MPS files, including gzip files, use the same `solve(path, client=client)` call on Linux, macOS, and Windows. See the [QP chapter](qp-modeller.md) for the continuous-QP curvature contract.

### Numeric input range

For local LP, MILP, and continuous-QP models, each nonzero constraint-matrix or Hessian coefficient must satisfy `1e-9 < abs(value) < 1e15`. Every objective coefficient and every finite row or variable bound must have magnitude below `1e20`; the local solver interprets values at that boundary as infinite. Integer coefficients and bounds must be exactly representable in binary64, which limits exact integers to `abs(value) <= 2**53`. Rescale a row or objective when a value lies outside these ranges. File inputs are rejected when loading would discard a matrix coefficient.

Indicator bodies use a bounded big-M reformulation for LP/MILP export. If the active case would lose more than the SDK's `1e-6` source-feasibility tolerance through floating-point cancellation, compilation raises a numerical-stability error before solving. Tighten the body-variable bounds or rescale the model; the SDK does not return an optimum for a numerically weakened indicator.

Local QUBO, HUBO, and Ising solving uses:

1. direct exhaustive evaluation when the polynomial has at most 20 active variables and at most 2,000,000 estimated evaluation operations;
2. native simulated annealing above that cutoff.

The exhaustive method evaluates binary assignments for QUBO/HUBO and spin assignments in `{-1, +1}` for Ising.

The annealer flips binary values for QUBO/HUBO and spin values for Ising. It evaluates HUBO terms at their original degree. The SDK recomputes the returned source objective from the original coefficients and assignment. For a MAX QUBO or HUBO, the search objective is sign-normalized and the result is restored to the source sense.

A polynomial compiled from `Model` can already contain constraint penalties, domain encodings, or auxiliary variables created by the documented modeller compiler. Local solving preserves that compiled QUBO/HUBO/Ising form and decodes the selected assignment back to source variables.

LP/MILP retains the linear solver's status and optimality-gap fields. Completed QUBO/HUBO/Ising results omit status and proof fields; use `result.info()` for termination and guarantee semantics.

## Local options

| Option | Classes | Default | Validation | Effect |
|---|---|---:|---|---|
| `time_limit` | LP/MILP | `60.0` | Positive finite non-Boolean number | Wall-clock solve limit in seconds |
| `mip_rel_gap` | MILP | `1e-4` | Non-negative finite non-Boolean number | Stop when the relative objective gap reaches this value |
| `node_limit` | MILP | unset | Positive non-Boolean integer | Maximum branch-and-bound nodes |
| `time_limit` | QP | `60.0` | Positive finite non-Boolean number | Wall-clock solve limit in seconds |
| `num_reads` | QUBO/HUBO/Ising | `8` | Positive non-Boolean integer; class limit below | Independent annealing runs |
| `num_sweeps` | QUBO/HUBO/Ising | `500` | Positive non-Boolean integer; class limit below | Annealing sweeps per run |
| `seed` | QUBO/HUBO/Ising | `0` | Integer from `0` through `2**32 - 1`; Booleans rejected | Random seed |

Options for the wrong class raise `ValueError`; they are not ignored. The exact polynomial strategy is selected deterministically from model work, so annealing controls affect solving only above the exact cutoff. Reads, sweeps, term count, and interaction degree jointly bound sampled local work. A time-limited LP/MILP run returns `status="feasible"` when it has an incumbent, otherwise `status="limit_reached"`.

A `HUBOProblem` may store `num_reads`, `num_sweeps`, and `seed` in `solver_config`; local solving uses them when the corresponding call argument is omitted. Other HUBO configuration keys are platform-only and local solving or inspection rejects them. Exhaustive enumeration does not use annealing settings and reports an empty settings dictionary through `result.info()`.

Provider support is checked again when `auto` crosses a local structural limit. HUBO `num_reads`, `num_sweeps`, and `seed` retain the same names on the platform. QUBO and Ising platform resources do not expose those controls, so fallback raises `CapabilityError` naming the unsupported option instead of discarding it. For LP/MILP, `time_limit`, `mip_rel_gap`, and `node_limit` populate `MILPSolverConfig.time_limit`, `.mip_rel_gap`, and `.mip_max_nodes`. Do not combine these keyword options with an explicit `config` in the same high-level call. Platform-only lifecycle options such as `poll_interval` and `request_timeout` belong to `mode="platform"`; they are not accepted by the local-first `auto` attempt.

## Structural limits

All thresholds live in `LOCAL_LIMITS` and are checked before solving. Counts are inclusive: a problem at the limit is accepted.

| Class | Variables | Constraints | Terms/nonzeros | Degree | Reads | Sweeps | Annealing work | File bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LP | 20,000 | 20,000 | 1,000,000 nonzeros | Not applicable | Not applicable | Not applicable | Not applicable | 50,000,000 |
| MILP | 20,000 | 20,000 | 1,000,000 nonzeros | Not applicable | Not applicable | Not applicable | Not applicable | 50,000,000 |
| QP | 1,000 | 20,000 | 1,000,000 linear / 100,000 Hessian nonzeros | 2 | Not applicable | Not applicable | Not applicable | Not applicable |
| QUBO | 250 | Not applicable | 5,000 terms | 2 by definition | 16 | 5,000 | 10,000,000 | Not applicable |
| HUBO | 100 | Not applicable | 1,500 terms | No separate limit | 16 | 2,500 | 10,000,000 | Not applicable |
| ISING | 250 | Not applicable | 5,000 terms | 2 by definition | 16 | 5,000 | 10,000,000 | Not applicable |

The common annealing-work ceiling makes the polynomial classes consistent while accounting for the greater work of high-degree terms. For terms with degrees `d`, the reported work is:

```text
num_reads * (
    sum(max(1, d))
    + num_sweeps * (active_variables + sum(d * max(1, d - 1)))
)
```

This mirrors the native solver's initial energy calculation and incident-term work for each sweep. A dense model can therefore exceed the work limit even when its variable and term counts are individually accepted. Reduce `num_reads` or `num_sweeps`, reduce the interaction structure, or choose an external or platform solver.

For polynomial inputs, `inspect()` reports:

```text
exact_enumeration_work = 2**active_variables * (
    max(1, active_variables + sum(degree(term)))
)
```

An active variable appears in at least one nonzero term. Isolated variables do not multiply exact-search or annealing work; local results assign them deterministically to `0` for binary models or `-1` for Ising models. The exact strategy requires both `active_variables <= 20` and `exact_enumeration_work <= 2_000_000`; crossing either threshold selects native annealing and is not a local-limit failure. Call `inspect(problem)` to obtain validated structural and work metrics. Provider limits are available separately through `LOCAL_LIMITS`; exceeding them during a local solve raises `LocalLimitExceeded`.

## Resource planning

Limits are resource-safety defaults for local development, CI, prototypes, and small practical models. `LOCAL_LIMITS` is public and can be inspected before a solve. For larger problems, use a solver directly or select platform execution.

Local execution limits cover matrix and Hessian size, enumeration work, and topology-weighted annealing work. Compare the compiled representation's inspection metrics with `LOCAL_LIMITS` when selecting an execution route. Execution time also depends on coefficients, sparsity, and problem structure.

Annealing quality varies with coefficients and interaction structure. Verify the source objective and, for a `Model`, call `feasibility()` when interpreting a returned candidate.

## Structured failures

`LocalLimitExceeded.as_dict()` returns:

```json
{
  "error": "local_limit_exceeded",
  "problem_class": "QUBO",
  "observed": {"variables": 501},
  "limits": {"variables": 250},
  "explanation": "variables=501 exceeds 250",
  "next_action": "reduce the model or call solve(..., mode='platform') explicitly; platform use may consume credits"
}
```

`CapabilityError.as_dict()` similarly returns the detected class, requested mode, reason, provider capabilities, and compatible alternatives.

A positive-size QUBO whose coefficients all normalize to zero is valid and solves locally. QpiAI Platform requires at least one nonzero QUBO coefficient; platform solving and submission reject this case before any request and direct the caller to local solving.

## Completed result contract

Every completed result starts with `problem_class`, `execution_mode`, `job_id`, `problem_id`, `solver_time_s`, and `compute_time_s`. Local identifiers are `None`. `solver_time_s` measures the algorithm call; `compute_time_s` includes preparation and result processing. Platform optimization results use the service-reported durations; unavailable measurements remain `None`. Use `runtime("ms", kind="compute")` or `runtime("us", kind="solver")` and the corresponding `compute_time_ms`/`compute_time_us` or `solver_time_ms`/`solver_time_us` properties for other units. The typed `solve_info` field preserves execution details in ordinary Python and JSON dumps; `info()` returns the same interpretation.

```python
import os

from qpiai_opt.opt_solvers import Client

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])
result = solve(problem, client=client)
result_class = result.problem_class
execution_mode = result.execution_mode
runtime_seconds = result.compute_time_s
runtime_milliseconds = result.runtime("ms")

payload = result.model_dump(mode="json")
serialized_runtime_fields = {
    name: payload[name] for name in ("solver_time_s", "compute_time_s")
}
```

Both measured durations serialize in seconds. Unit properties calculate views on demand; an unavailable duration stays `None`, and an unrepresentable unit conversion raises `ValueError`.

LP, MILP, QP, QUBO, HUBO, and Ising result constructors accept `None` or stripped nonempty strings for job and problem IDs; local results require both IDs to be `None`. Durations accept `None` or finite nonnegative non-Boolean real values, normalized to `float`. Invalid identity, timing, or attached solve-information fields raise `pydantic.ValidationError`.

### Common solve information

The normal result stays specific to its problem class. Use `info()` only when you need the same interpretation across LP, MILP, QP, QUBO, HUBO, and Ising:

```python
info = result.info()

info_payload = info.model_dump(mode="json")
```

`info()` returns a detached deep copy of the validated `SolveInfo` stored in `result.solve_info`. Ordinary `model_dump()` and `model_dump(mode="json")` include that field, so method, settings, and guarantees travel with the completed result.

For a manually constructed LP, MILP, QP, QUBO, HUBO, or Ising result with `solve_info=None`, `info()` derives an interpretation from the stored result fields. It does not recover execution settings or populate `solve_info`. The [common information reference](api-solvers.md#class-solveinfo) defines constructor validation and consistency requirements.

Persist the ordinary result dump and validate it with the corresponding class-specific result model when loading:

```python
record = result.model_dump(mode="json")
restored = type(result).model_validate(record)
restored_info = restored.info()
```

Serialize `solve_info` with the result to retain method settings. See [result deserialization](../automation.md#blocking-solve) for result consumers.

| Field | Meaning |
| --- | --- |
| `problem_class` | Identified mathematical class. |
| `execution_mode` | `local` or `platform`. |
| `provider` | SDK or platform provider that executed the solve. |
| `solver` | Solver name when the SDK can identify it; otherwise `None`. |
| `method` | Mathematical or sampling method. Local polynomial results use `exhaustive-enumeration` or `simulated-annealing`. |
| `termination` | `optimal` for a proven optimum; `completed` when a solve finished without proof or a more specific reported stop condition; otherwise `limit_reached`, `infeasible`, `unbounded`, `failed`, or `unknown`. |
| `has_usable_solution` | Whether the result contains a source-feasible candidate suitable for use. |
| `solution_feasible` | Source-model feasibility when it can be established; otherwise `None`. |
| `optimality_proven` | Whether global optimality was established; `None` only when the available result cannot answer. |
| `guarantee` | `global_optimum`, `best_found`, `best_bound`, `feasible_only`, an infeasibility/unboundedness proof, `none`, or `unknown`. A best bound does not require an incumbent. |
| `source_objective` | Objective in the source model's sense and coordinates, when available. |
| `transformed` | Whether the submitted source was compiled or converted before solving; `None` when unknown. |
| `settings` | Known settings that materially describe the selected method, such as reads, sweeps, and seed. |

The provider and method identify the execution path. The `solver` field is runtime diagnostic metadata; use `problem_class`, `execution_mode`, and `method` to interpret the mathematical result.

| Execution | `provider` | `method` |
| --- | --- | --- |
| Local LP | `qpiai-opt-local` | `linear-programming` |
| Local MILP | `qpiai-opt-local` | `mixed-integer-linear-programming` |
| Local QP | `qpiai-opt-local` | `convex-quadratic-programming` |
| Local QUBO/HUBO/Ising | `qpiai-opt-local` | `exhaustive-enumeration` or `simulated-annealing` |
| Platform LP/MILP | `qpiai-opt-platform` | `linear-programming` or `mixed-integer-linear-programming` |
| Platform QUBO | `qpiai-opt-platform` | `QUBO` |
| Platform HUBO | `qpiai-opt-platform` | `HUBO` |
| Platform Ising | `qpiai-opt-platform` | `QUBO`, because platform execution uses the explicit Ising-to-QUBO conversion |

Local annealing settings contain `num_reads`, `num_sweeps`, and `seed`; exact enumeration reports `{}` because those controls were not used. Platform MILP settings contain the submitted `MILPSolverConfig`, including SDK defaults, when the solver instance retained the request or the backend returned a scalar configuration. Platform HUBO settings contain the validated public configuration when the source problem is available. Platform QUBO and Ising settings are always `{}` because that platform configuration is not public.

For local polynomial solving, exhaustive enumeration proves the optimum of the native polynomial. If a decoded candidate violates the source model, `has_usable_solution` and `solution_feasible` are false and its guarantee is `none`. Source-feasible simulated-annealing and platform polynomial candidates use `best_found`, without an optimality proof.

### QUBO response

```json
{
  "problem_class": "QUBO",
  "execution_mode": "local",
  "job_id": null,
  "problem_id": null,
  "solver_time_s": 5.73e-05,
  "compute_time_s": 0.000682,
  "solve_info": {
    "problem_class": "QUBO",
    "execution_mode": "local",
    "provider": "qpiai-opt-local",
    "solver": "native-polynomial",
    "method": "exhaustive-enumeration",
    "termination": "optimal",
    "has_usable_solution": true,
    "solution_feasible": true,
    "optimality_proven": true,
    "guarantee": "global_optimum",
    "source_objective": -2.0,
    "transformed": false,
    "settings": {}
  },
  "num_nodes": 2,
  "objective": -2.0,
  "variable_assignment": {
    "a": 1,
    "b": 0
  },
  "solution": [
    1,
    0
  ]
}
```

`solution` is in QUBO index order. `variable_assignment` uses supplied names, source-model names, or generated `x[index]` names. `num_nodes` is the QUBO variable count for a local result and the returned platform node count for a platform result. See the [QUBO result field reference](qubo-solver.md#result-interpretation).

### HUBO response

```json
{
  "problem_class": "HUBO",
  "execution_mode": "local",
  "job_id": null,
  "problem_id": null,
  "solver_time_s": 2.83e-05,
  "compute_time_s": 0.0002445,
  "solve_info": {
    "problem_class": "HUBO",
    "execution_mode": "local",
    "provider": "qpiai-opt-local",
    "solver": "native-polynomial",
    "method": "exhaustive-enumeration",
    "termination": "optimal",
    "has_usable_solution": true,
    "solution_feasible": true,
    "optimality_proven": true,
    "guarantee": "global_optimum",
    "source_objective": -3.0,
    "transformed": false,
    "settings": {}
  },
  "num_variables": 3,
  "objective": -3.0,
  "variable_assignment": {
    "a": 1,
    "b": 1,
    "c": 1
  },
  "solution": [
    1,
    1,
    1
  ]
}
```

`solution` follows deterministic label order and contains only binary values. See the [HUBO result field reference](hubo-solver.md#result-interpretation).

### Ising response

```json
{
  "problem_class": "ISING",
  "execution_mode": "local",
  "job_id": null,
  "problem_id": null,
  "solver_time_s": 1.39e-05,
  "compute_time_s": 0.0002342,
  "solve_info": {
    "problem_class": "ISING",
    "execution_mode": "local",
    "provider": "qpiai-opt-local",
    "solver": "native-polynomial",
    "method": "exhaustive-enumeration",
    "termination": "optimal",
    "has_usable_solution": true,
    "solution_feasible": true,
    "optimality_proven": true,
    "guarantee": "global_optimum",
    "source_objective": -2.0,
    "transformed": false,
    "settings": {}
  },
  "num_variables": 2,
  "objective": -2.0,
  "variable_assignment": {
    "s0": -1,
    "s1": -1
  },
  "solution": [
    -1,
    -1
  ]
}
```

`solution` contains spins in `{-1, +1}` and `objective` is in the source Ising sense. See [Ising solving](ising-modeller.md#solve).

QUBO, HUBO, and Ising keep the same compact response shape for exhaustive and sampled candidates. Use `result.info()` for method and guarantee information. Platform job lifecycle state remains available through `status(client, job_id)` and the platform website.

### LP and MILP response

```python
linear_result = {
    "status": result.status,
    "solution": result.solution,
    "objective": result.objective,
    "best_bound": result.best_bound,
    "optimality_gap": result.optimality_gap,
    "optimality_proven": result.info().optimality_proven,
}
```

`model_status` preserves the solver's diagnostic text. The normalized `status` has these meanings:

| Status | Meaning | Safe use of `solution` |
| --- | --- | --- |
| `optimal` | The solver reported an optimum; a MILP incumbent and bound also agree. | Present and validated. |
| `feasible` | A valid incumbent exists, but optimality was not established. | Present; treat it as an incumbent. |
| `limit_reached` | A work/time limit ended the solve without a valid incumbent. | `None`. |
| `infeasible` | No assignment satisfies the model. | `None`. |
| `unbounded` | The source objective has no finite optimum in its improving direction. | `None` or a finite point; no optimum. |
| `failed` | The solver did not provide a valid successful outcome. | `None`. |

Some unbounded QP solves include a feasible point found before the unbounded direction was established. In that case `info().has_usable_solution` and `solution_feasible` are true, while `termination` remains `unbounded` and the point must not be presented as an optimum.

`objective`, `best_bound`, and `optimality_gap` use the source MIN/MAX coordinates and include the source objective constant. Optional dual, basis, ranging, pool, and IIS fields remain `None` unless requested and supplied. The [MILP result reference](milp-solver.md#result-interpretation) defines every field and optional diagnostic model.

### QP response

```python
qp_result = {
    "status": result.status,
    "solution": result.solution,
    "objective": result.objective,
    "iteration_count": result.iteration_count,
    "optimality_proven": result.info().optimality_proven,
}
```

QP uses the same normalized status meanings where applicable. It has no MILP node, bound, gap, basis, pool, or IIS fields. Continuous QP is local-only; see the [QP solve and verification guide](qp-modeller.md#solve-and-verify).

### Verify before using a candidate

```python
from qpiai_opt.opt_solvers import feasibility

values = (
    result.solution if result.problem_class in {"LP", "MILP", "QP"} else result.variable_assignment
)

if values is not None:
    report = feasibility(model, values)
    source_feasible = report["feasible"]
```

`feasibility()` accepts model/source names and any deterministic auxiliary columns returned for that model. It rejects unknown keys so a misspelled name cannot be silently ignored. Its complete return shape is documented in the [feasibility reference](api-solvers.md#function-feasibility).
