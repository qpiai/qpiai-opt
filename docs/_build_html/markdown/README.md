# QpiAI Opt SDK

<!-- cspell:ignore bibtex -->

QpiAI Opt is a Python SDK for mathematical optimization, with algebraic modelling, local solvers, QpiAI Platform execution, and standard model interchange. Its native higher-order HUBO solver evaluates higher-order interactions directly, preserving the polynomial's original energy landscape without quadratization or reduction-induced approximations. Gate-based optimization is available with access to QpiAI simulators and QPUs.

The modelling SDK is open source under Apache-2.0; the local solver runtime is distributed as a native binary. Python 3.10–3.14 is supported on Linux, macOS, and Windows.

Docs: [QpiAI Opt SDK Documentation](https://opt-docs.qpiai.tech/).

## Install

Install:

```bash
python -m pip install qpiai-opt
```

The distribution name is `qpiai-opt`; the import namespace is `qpiai_opt`. The base package includes the QpiAI Opt local solver and supports local LP, MILP, QP, QUBO, HUBO, and Ising solving. Install an extra only for its integration:

```bash
python -m pip install "qpiai-opt[quantum]"
python -m pip install "qpiai-opt[ommx]"
python -m pip install "qpiai-opt[all]"
```

`quantum` installs `qpiai-quantum==0.3.0`, `ommx` adds OMMX interchange, and `all` installs both. See [gate-based optimization](docs/quantum-optimization.md) and [OMMX interchange](docs/interoperability.md#ommx-bridge) for requirements and usage.

Read the [user manual](docs/index.md) in order for installation, modelling, solving, interoperability, automation, and complete API reference.

## First local solve

Local solving through an Opt `Client` verifies the Opt key online before computation; disconnected or unverifiable authentication stops execution. Register for a free Opt API key at [QpiAI Opt](https://opt.qpiai.tech/) and place it in your application's secret store. The code below reads it from the `QPIAI_OPT_API_KEY` environment variable.

```python
import os

from qpiai_opt.opt_modeller import Binary, Model
from qpiai_opt.opt_solvers import Client, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])

x = Binary("x")
y = Binary("y")

model = Model("selection")
model.maximize(6 * x + 4 * y)
model.add_constraint(x + y <= 1, label="capacity")

result = solve(model, client=client)  # mode="local" is the default
print(result.problem_class)  # "MILP"
print(result.execution_mode)  # "local"
print(result.solution)  # {"x": 1.0, "y": 0.0}
print(result.objective)  # source objective, including MAX sense
```

`solve()` accepts a `Model`, `QUBOProblem`, `HUBOProblem`, `IsingProblem`, `MILPProblem`, an LP/MPS path (plain or gzip), or the documented linear/polynomial dictionaries. It returns the same problem-class-specific result shape in local and platform modes.

Numeric inputs use IEEE-754 binary64 semantics. Ordinary decimal-to-binary rounding is expected; the SDK does not provide arbitrary-precision decimal arithmetic. Keep values within the finite binary64 range and scale a model whose coefficients or bounds are extremely large or small. An integer larger than `2**53` may not retain every digit when converted to binary64, so use smaller or scaled values where exact integer meaning matters. `Decimal` values are not accepted as numeric inputs; use a supported Python or NumPy real value instead. The detailed numeric rules are in the [canonical IR reference](docs/model-ir.md#numeric-representation).

## Execution scope

Registration, API-key issuance, and local solving are free. Local solving through an Opt `Client` verifies the supplied Opt key online before computation; disconnected or unverifiable authentication stops execution. No optimization model is sent to the authentication endpoint. Gate-based optimization on local simulators and QpiAI simulators and QPUs requires an active `QpiAIQuantumAuth.login(QCLOUD_API_KEY)` and fresh QCloud verification. Obtain a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/). Modelling, validation, inspection, conversion, and result evaluation remain credential-free.

| Call | Credentials | Can create paid work | Behavior |
|---|---:|---:|---|
| `solve(problem, client=client)` | Opt API key | no | authenticated QpiAI Opt local solving |
| `solve(problem, mode="local", client=client)` | Opt API key | no | authenticated QpiAI Opt local solving |
| `solve(problem, mode="auto", client=client)` | Opt API key | no | local only unless explicitly enabled |
| `solve(..., mode="auto", allow_platform=True, client=client)` | Opt API key | yes | platform fallback only after a local limit |
| `solve(..., mode="platform", client=client)` | Opt API key | yes | platform directly |
| `solve(..., mode="local", algorithm=QAOA(...))` | QCloud API key | no | authenticated QpiAI local quantum simulator |
| `solve(..., mode="platform", algorithm=QAOA(...))` | QCloud API key | yes | QCloud circuit execution |
| `submit(problem, client=client)` | Opt API key | yes | exactly one nonblocking submission attempt |

Selecting platform execution with an authenticated `Client` authorizes the submission and any associated credit use.

In `auto` mode, platform fallback requires `allow_platform=True` and occurs only after `LocalLimitExceeded`. Authentication errors propagate. The limit exception includes `observed`, `limits`, `explanation`, and `next_action`. Job creation has no idempotency key; after an ambiguous submission timeout, list existing jobs before retrying.

See [Local solving and routing](docs/local-solving.md) for accepted inputs, limits, options, statuses, and failure recovery.

## Modelling and representations

The algebraic modeller supports:

- continuous, integer, binary, spin, semi-continuous, semi-integer, fixed-point fractional, and mixed domains;
- linear relations, indicators, SOS1/SOS2, binary logic, and custom penalties;
- constrained QUBO and HUBO compilation with source-sample decoding;
- native Ising fields and couplings;
- LP/MPS/gzip, sparse matrix, QUBO/HUBO dictionaries, and canonical JSON IR;
- explicit QUBO↔Ising, HUBO→QUBO, QUBO→MILP, and MILP→QUBO conversions;
- multidimensional `IndexedVariables` and bound/coefficient/RHS parameters;
- convex continuous QP with a documented Hessian convention;
- exact SOS2 piecewise-linear relationships and inspectable transformations;
- QAOA, VQE, SamplingVQE, RecursiveQAOA, and ADAPT-QAOA execution through `qpiai-quantum` circuits, simulation, and QpiAI simulators and QPUs access;
- optional OMMX import/export for supported model structures.

Reference guides:

- [MILP modeller](docs/milp-modeller.md)
- [Continuous QP modeller](docs/qp-modeller.md)
- [QUBO modeller](docs/qubo-modeller.md)
- [HUBO modeller](docs/hubo-modeller.md)
- [Ising modeller](docs/ising-modeller.md)
- [Indexed variables, parameters, and updates](docs/modelling-workflows.md)
- [Canonical IR](docs/model-ir.md)
- [Gate-based optimization](docs/quantum-optimization.md) and [gate-based optimization API](docs/api-quantum.md)
- [External interoperability](docs/interoperability.md)
- [Piecewise-linear modelling](docs/piecewise-linear.md)
- [Transformation inspection](docs/transformations.md)

## Explicit platform execution

Read the credential from your application's environment or secret manager. This call explicitly requests paid platform execution:

```python
import os

from qpiai_opt.opt_solvers import Client, solve

API_KEY = os.environ["QPIAI_OPT_API_KEY"]

client = Client(api_key=API_KEY)
result = solve(model, mode="platform", client=client)
print(result.model_dump(mode="json"))
```

The default endpoint is `https://opt-server.qpiai.tech/api`. Platform mode creates a problem and job and can consume credits. For nonblocking control use the automation `submit()`, `status()`, `result()`, and `cancel()` operations, or a solver instance's nonblocking methods.

The [platform client reference](docs/platform-client.md) covers account, organization, membership, workspace, project, experiment, problem, job, credit usage and allocations, compute-resource, storage, SSE, and retry behavior. Solver-specific request and response fields are in:

- [QUBO solver](docs/qubo-solver.md)
- [HUBO solver](docs/hubo-solver.md)
- [MILP solver](docs/milp-solver.md)

## Automation-safe Python API

```python
from qpiai_opt.opt_solvers import (
    classify,
    feasibility,
    inspect,
    provider_capabilities,
    schemas,
)

print(classify(model))
print(inspect(model))
print(provider_capabilities())
print(feasibility(model, {"x": 1, "y": 0}))
print(schemas()["solve_result"])
```

These functions return strings, dictionaries, or lists for programmatic use. See [Automation and agent workflows](automation.md) for workflow procedures and request schemas.

## Result contract

Results include the problem class, execution mode, identifiers, source objective, and `solver_time_s`/`compute_time_s` in seconds. `result.info()` reports the method, termination, feasibility, and optimality guarantee.

```python
info = result.info()
print(info.method, info.termination, info.guarantee)
```

| Class | Additional fields |
|---|---|
| QUBO | `num_nodes`, `objective`, binary-list `solution`, `variable_assignment`, optional `job_id`/`problem_id` |
| HUBO | `num_variables`, `objective`, binary-list `solution`, `variable_assignment`, optional `job_id`/`problem_id` |
| Ising | `num_variables`, `objective`, spin-list `solution`, `variable_assignment`, optional `job_id`/`problem_id` |
| LP/MILP | `sense`, `status`, `model_status`, `objective`, `solution`, source-coordinate bound/gap, and documented linear diagnostics |
| QP | `sense`, `status`, `model_status`, `objective`, `solution`, and QP iteration count |
| Gate-based optimization algorithms | `quantum` diagnostics and either a source optimization candidate or a generic Hamiltonian `eigenvalue`; see [gate-based optimization results](docs/api-quantum.md#class-quantumresult) |

The `solve_info` field stores execution details in serialized results; `info()` returns a detached view. Platform job status describes execution progress, while solve information describes the mathematical outcome. Serialize results with `model_dump(mode="json")`. See the [completed result contract](docs/local-solving.md#completed-result-contract) for fields, runtime units, statuses, and source-variable interpretation.

## Development and releases

Source and development are hosted at [qpiai/qpiai-opt](https://github.com/qpiai/qpiai-opt). See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, documentation builds, release checks, and community guidelines.

Use the [bug reporting guide](REPORTING_BUGS.md) for reproducible defects, the [feature request guide](FEATURE_REQUESTS.md) for proposals, and [private vulnerability reporting](https://github.com/qpiai/qpiai-opt/security/advisories/new) for security issues. The package is licensed under the [Apache License 2.0](LICENSE); [third-party notices](THIRD_PARTY_NOTICES) identify incorporated components and their licenses.

We follow Semantic Versioning (SemVer).

## Citation

When QpiAI Opt materially supports published work, cite the exact version. Repository hosts can read the structured [CITATION.cff](CITATION.cff) metadata. Plain-text citation:

> QpiAI. *QpiAI Opt SDK*, version 0.0.9, 2026.
> https://github.com/qpiai/qpiai-opt

BibTeX:

```bibtex
@software{qpiai_opt_0_0_9,
  author  = {{QpiAI}},
  title   = {QpiAI Opt SDK},
  version = {0.0.9},
  year    = {2026},
  url     = {https://github.com/qpiai/qpiai-opt}
}
```
