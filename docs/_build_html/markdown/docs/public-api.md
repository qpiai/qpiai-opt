# Public Python API

This index covers the supported import namespaces. Detailed reference pages preserve exact signatures; the workflow chapters explain mathematical meaning, input contracts, returned fields, and corrective actions.

| Namespace | Exact reference |
| --- | --- |
| `qpiai_opt` | Root package; exposes the `opt_modeller` and `opt_solvers` package attributes only |
| `qpiai_opt.opt_modeller` | [Modeller API](api-modeller.md) |
| `qpiai_opt.opt_modeller.formats` | [Representation utilities](api-formats.md) |
| `qpiai_opt.opt_modeller.compiler` | [Binary expression lowering](api-formats.md#function-lower_expression) |
| `qpiai_opt.opt_solvers` | [Common solver API](api-solvers.md) |
| `qpiai_opt.opt_solvers.qubo` | [QUBO solver API](api-qubo.md) |
| `qpiai_opt.opt_solvers.hubo` | [HUBO solver API](api-hubo.md) |
| `qpiai_opt.opt_solvers.milp` | [MILP solver API](api-milp.md) |
| `qpiai_opt.opt_solvers.ising` | [Ising problem API](api-solvers.md#class-isingproblem) |
| `qpiai_opt.quantum` | [Gate-based optimization API](api-quantum.md) and [gate-based optimization workflow](quantum-optimization.md) |
| `qpiai_opt.opt_modeller.utils.exceptions` | [Modeller exceptions](exceptions.md#modeller-exceptions) |
| `qpiai_opt.opt_modeller.constraints` | [Relational and logical base types](api-modeller.md#relational-constraints), also exported alongside the concrete constraints |

The namespaces listed above are the supported import locations. Import public objects from the listed child namespaces; the root package does not re-export their symbols. The reference documents every supported object available from them.

## Core model and algebra

| Symbol | Purpose |
| --- | --- |
| `Model` | Owns objective, constraints, variables, parameters, export, compilation, summary, and transformation records. |
| `Expression`, `Term` | Validated polynomial AST and one coefficient/factor term. |
| `quicksum`, `dot` | Construct symbolic sums and dot products; empty inputs return a constant-zero expression. |
| `IndexedVariables` | Deterministic multidimensional variable container. |
| `Parameter` | Validated mutable scalar bound to supported model coefficients, RHS values, or bounds. |

```python
from qpiai_opt.opt_modeller import Binary, Model, Parameter, quicksum

x = [Binary(f"x[{i}]") for i in range(3)]
model = Model("api")
model.minimize(quicksum((i + 1) * x[i] for i in range(3)))
capacity = Parameter("capacity", 1)
model.add_parameter(capacity)
```

See [modelling workflows](modelling-workflows.md).

## Variable domains and helpers

`Variable` is the common base. `Binary`, `Spin`, `Integer`, `Continuous`, `SemiContinuous`, `SemiInteger`, `Fraction`, and `Mixed` validate their documented domains. `create_binary_array` and `create_spin_array` create named arrays, including empty arrays when `size=0`.

```python
from qpiai_opt.opt_modeller import Binary, Continuous, Integer, Spin

b = Binary("selected")
i = Integer("units", 0, 10)
x = Continuous("flow", 0, 100)
s = Spin("orientation")
```

Domain-specific rules are in the [MILP](milp-modeller.md), [QUBO](qubo-modeller.md), [HUBO](hubo-modeller.md), and [Ising](ising-modeller.md) chapters.

## Constraints and piecewise modelling

`SOS1`, `SOS2`, and `Indicator` are exact MILP constructs. `AND`, `OR`, `XOR`, and `NOT` construct binary logical constraints. `CustomPenalty` is an explicit soft polynomial energy. `Model.estimate_penalty_weight(constraint)` returns the compiler's automatic hard-constraint weight without changing the model. `PiecewiseLinear` is the descriptor returned by the model's exact SOS2 interpolation method.

```python
from qpiai_opt.opt_modeller import Binary, Indicator, SOS1

active, a, b = Binary("active"), Binary("a"), Binary("b")
model.add_constraint(SOS1("choose", [a, b]))
model.add_constraint(Indicator(active, 1, a + b <= 1))
```

See [MILP constraints](milp-modeller.md), [QUBO penalties](qubo-modeller.md), and [piecewise-linear relationships](piecewise-linear.md).

## Encoders and representation converters

`encode_integer_binary`, `encode_continuous`, and `encode_fraction` return the single domain encoding used by compilation. `hubo_to_qubo`, `hubo_to_ising`, `qubo_to_milp`, `milp_to_qubo`, `qubo_to_ising`, and `ising_to_qubo` perform explicit exact representation transformations within their stated scope.

```python
from qpiai_opt.opt_modeller import hubo_to_qubo, qubo_to_ising

reduced = hubo_to_qubo({("a", "b", "c"): -2})
ising = qubo_to_ising(reduced.qubo, constant=reduced.offset)
conversion_steps = reduced.transformations
```

The conversion records are `HUBOToQUBOConvert`, `QUBOToMILPConvert`, `MILPToQUBOConvert`, `QUBOToIsingConvert`, `HUBOToIsingConvert`, and `IsingToQUBOConvert`. Their dataclass field assignments are frozen, but nested dictionaries and retained models remain mutable. See their exact [conversion contracts](api-modeller.md#function-hubo_to_qubo), [transformation inspection](transformations.md), and [interoperability](interoperability.md).

## Canonical IR

`IR_VERSION`, `to_ir`, `from_ir`, `dumps`, `loads`, `dump`, and `load` expose the versioned JSON-safe process/storage representation.

```python
from qpiai_opt.opt_modeller import dumps, loads

serialized = dumps(model)
restored = loads(serialized)
```

See [canonical model IR](model-ir.md) for version and trust boundaries.

## Common solver flow

`classify` and `inspect` perform no solve. `solve` routes a blocking solve. `provider_capabilities` describes local/platform class support. `LOCAL_LIMITS` contains the machine-readable local size limits.

```python
import os

from qpiai_opt.opt_solvers import Client, inspect, provider_capabilities, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])

inspection = inspect(model)
capabilities = provider_capabilities()
result = solve(model, mode="local", client=client)
```

See [local solving](local-solving.md).

## Automation operations

`convert`, `feasibility`, `submit`, `status`, `result`, `cancel`, and `schemas` form the strict Python automation surface.

```python
from qpiai_opt.opt_solvers import feasibility, schemas

result_schema = schemas()["solve_result"]
feasibility_report = feasibility(model, result.solution)
```

`submit` is explicitly platform-only and may consume credits. See the [agent operational contract](../automation.md).

## Gate-based optimization

`qpiai_opt.quantum` exports `QAOA`, `VQE`, `SamplingVQE`, `RecursiveQAOA`, `ADAPTQAOA`, `WarmStart`, `CVaR`, `XYMixer`, `QuantumRun`, `QuantumResult`, `QuantumDiagnostics`, `LogicalCircuitInfo`, `QuantumSample`, `QuantumSolveInfo`, and `build_circuit`. It also re-exports the `qpiai_quantum.Circuit`, `QubitHamiltonian`, and `QpiAIQuantumAuth` types without exposing lower-level QCloud clients or job managers.

Obtain a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/) and supply it to `QpiAIQuantumAuth.login`.

```python
import os

from qpiai_opt.opt_solvers import solve
from qpiai_opt.quantum import QAOA, QpiAIQuantumAuth, QuantumRun

QpiAIQuantumAuth.login(api_key=os.environ["QPIAI_QCLOUD_API_KEY"])

qubo = Model("quantum_selection")
qubo.minimize(-a - b + 2 * a * b)
problem = qubo.compile(target="QUBO")
result = solve(
    problem,
    algorithm=QAOA(reps=2),
    execution=QuantumRun(shots=1024, seed=7),
)
```

For QCloud execution, omit `QuantumRun.device_name` for the default or supply a supported device name chosen by the user or application. `QpiAIQuantumAuth.list_compute_resources(display=False)` is a separate, optional view of the authenticated user's resources; it does not determine or validate the selection. Authentication and resource inspection remain QpiAI Quantum SDK operations, and the optimization SDK adds no account-management client. See [gate-based optimization](quantum-optimization.md) for Hamiltonian conventions, supported compositions, device selection, execution boundaries, inspection, and result semantics.

## OMMX

`to_ommx` and `from_ommx` move the supported model subset through the optional OMMX ecosystem. `Model.to_ommx()` and `Model.from_ommx()` are equivalent conveniences.

```python
from qpiai_opt.opt_modeller import from_ommx, to_ommx

exchange_model = Model("exchange")
flow = Continuous("exchange_flow", 0, 100)
exchange_model.minimize(2 * flow)
exchange_model.add_constraint(flow >= 10)
instance = to_ommx(exchange_model)
restored = from_ommx(instance)
```

Install `qpiai-opt[ommx]`; unsupported constructs raise before conversion. See [interoperability](interoperability.md#ommx-bridge).

## Platform client and resource models

`Client` exposes authenticated platform operations. `Problem`, `Job`, `JobStatus`, `ComputeResource`, `Organization`, `Workspace`, `Project`, `Experiment`, `Invitation`, `UserSummary`, `SSEEvent`, `CreditUsage`, `CreditUsageByResource`, `CreditUsageJob`, `CreditResourceUsage`, and `CreditQuota` validate returned platform data. The client exposes read-only credit usage and quota methods; see [credit usage and allocations](platform-client.md#credit-usage-and-allocations).

```python
import os

from qpiai_opt.opt_solvers import Client, JobStatus

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])
jobs = client.list_jobs(status=JobStatus.RUNNING)
resources = client.list_solvers(method="QUBO", active_only=True)  # advanced pinning only
```

Job-list filters use `pending`, `running`, `completed`, `failed`, and `cancelled`. See [platform client](platform-client.md). Normal solve calls do not need a `compute_resource_id`.

## Results and errors

`SolveResult` is the union of completed LP, MILP, QP, QUBO, HUBO, and Ising results. `SolveInfo` is their common interpretation; gate-based optimization algorithms return the separate `QuantumResult` and `QuantumSolveInfo` types. `schemas()["solve_result"]` covers both result families. `IsingSolveResult` and `QPSolveResult` are directly exported; QUBO, HUBO, and MILP result types live in their solver subpackages. Each of `qpiai_opt.opt_solvers.qubo`, `qpiai_opt.opt_solvers.hubo`, and `qpiai_opt.opt_solvers.milp` exports its class-specific `SolveResult`, `JobSubmissionResponse`, and `JobStatusResponse` alongside its problem/config and solver classes.

```python
from qpiai_opt.opt_solvers import SolveInfo

result_identity = (result.problem_class, result.execution_mode, result.compute_time_s)
solve_information = result.info().model_dump(mode="json")
serialized_result = result.model_dump(mode="json")
```

`info()` returns the common interpretation carried in the serialized `solve_info` field. Ordinary class-specific result dumps preserve it. See the [common solve information](local-solving.md#common-solve-information) table.

`PlatformError` is the solver/API base error. `APIError`, `AuthenticationError`, `CapabilityError`, `JobFailedError`, `JobCancelledError`, and `LocalLimitExceeded` distinguish corrective actions and expose structured details where applicable.

```python
from qpiai_opt.opt_solvers import LocalLimitExceeded

try:
    solve(model, client=client)
except LocalLimitExceeded as error:
    limit_details = error.as_dict()
```

See [exception reference](exceptions.md), [result semantics](local-solving.md#completed-result-contract), [local failures](local-solving.md#structured-failures), and [platform recovery](platform-client.md).
