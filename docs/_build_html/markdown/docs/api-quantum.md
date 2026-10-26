# Gate-based optimization API reference

Import the objects below from `qpiai_opt.quantum`. Install `qpiai-opt[quantum]` before using circuits, Hamiltonians, inspection, or execution. The [gate-based optimization guide](quantum-optimization.md) defines the Hamiltonian mapping, parameter order, algorithms, authentication, and sample selection.

Algorithm configurations are frozen dataclasses. Numeric initial-point sequences, basis-state sequences, and custom operator pools are copied to tuples. Execution and auxiliary configurations are Pydantic models with keyword-only constructors, forbidden unknown fields, and frozen field assignment. Circuit objects and callbacks remain caller-owned; do not mutate them during a workflow. Pydantic result models support `model_validate`, `model_validate_json`, `model_dump`, `model_dump_json`, and `model_json_schema` through the [Pydantic model API](https://docs.pydantic.dev/latest/api/base_model/).

## Class: `QAOA`

```text
QAOA(reps=1, parameterization="standard", optimizer="cobyla",
     max_iterations=100, initial_point=None, aggregation=None,
     warm_start=None, mixer=None, initial_state=None)
```

| Parameter | Type | Contract |
| --- | --- | --- |
| `reps` | `int` | Positive layer count; Boolean values are rejected. |
| `parameterization` | `str` | `"standard"` or `"multi_angle"`; case and hyphens are normalized. |
| `optimizer` | `str \| Callable` | Named optimizer or the custom optimizer contract below. |
| `max_iterations` | `int` | Positive optimizer budget passed to the selected optimizer. Its interpretation is optimizer-specific. |
| `initial_point` | `Sequence[float] \| None` | Finite vector of exactly the parameter count; `None` starts QAOA at zero. |
| `aggregation` | `CVaR \| None` | `None` uses sampled expectation. |
| `warm_start` | `WarmStart \| None` | Supplies preparation and tilted mixer; conflicts with `mixer` and `initial_state`. |
| `mixer` | `XYMixer \| Callable \| None` | `None` uses X mixing. An XY mixer requires an explicit initial state. A callable requires standard parameterization. |
| `initial_state` | `Sequence[int] \| Circuit \| None` | One basis bit per qubit, or a same-width circuit without measurements. `None` prepares the uniform superposition. Bits are in increasing qubit order, unlike printed measurement strings. |

The supported problem input is an explicit QUBO or Ising problem or its documented dictionary form, or an exactly convertible identity/Z/ZZ `QubitHamiltonian`. At least one qubit is required. An algebraic `Model` must be compiled explicitly first.

### Methods

```text
parameter_count(ising) -> int
build_circuit(problem, parameters, measurements=False) -> Circuit
run(problem, *, execution=None, mode="local") -> QuantumResult
```

`parameter_count` accepts the supported QAOA problem representations. Standard parameterization returns `2 * reps`; multi-angle parameterization returns `reps * (nonzero_fields + nonzero_couplings + mixer_generators)`. There is one X or warm-start generator per qubit, or one generator per XY edge. The identity term has no parameter. Use `inspect(problem, algorithm=algorithm)` for full compatibility checks.

`build_circuit` requires a finite parameter vector of that exact length. It returns a numeric `Circuit`, optionally with measurements, without execution. `run` executes the same algorithm as `solve(problem, algorithm=algorithm, execution=execution, mode=mode)` and returns the same typed result. All direct `run` calls retain the authentication and mode restrictions of routed solving.

## Classes: `VQE` and `SamplingVQE`

```text
VQE(ansatz="standard", optimizer="cobyla", max_iterations=100,
    initial_point=None)
SamplingVQE(ansatz="standard", optimizer="cobyla", max_iterations=100,
            initial_point=None, aggregation=None)
```

| Parameter | Type | Contract |
| --- | --- | --- |
| `ansatz` | `str \| Circuit \| Callable[[int], Circuit]` | `"standard"`, `"hardware_efficient"`, `"two_local"`, a circuit, or a factory receiving the problem's qubit count. |
| `optimizer` | `str` | A named optimizer; custom optimizer callbacks are rejected. |
| `max_iterations` | `int` | Positive optimizer budget; Boolean values are rejected. |
| `initial_point` | `Sequence[float] \| None` | Finite vector with one value per bindable circuit angle. `None` lets VQE initialize through the QpiAI Quantum SDK; SamplingVQE samples uniformly on `[0, 2*pi)`. A local execution seed controls random initialization. |
| `aggregation` | `CVaR \| None` | SamplingVQE only; `None` uses sampled expectation. |

Both classes expose `build_circuit(problem, parameters, measurements=False) -> Circuit` and `run(problem, *, execution=None, mode="local") -> QuantumResult`. They accept explicit optimization QUBO/Ising input or a real Pauli `QubitHamiltonian`. SamplingVQE requires diagonal I/Z terms; VQE also accepts X/Y terms. Custom ansätze must have exactly the problem width, contain no measurements, and have at least one bindable parameter. Each parametric gate must have exactly one angle. Parameters bind in circuit operation order; inspection reports the count.

Generic VQE returns the optimized variational energy as `eigenvalue`. Generic SamplingVQE returns the lowest observed computational-basis energy, with the optimized expectation or CVaR in `quantum.algorithm_metadata["optimized_aggregate"]`. Optimization-origin runs instead return a source-feasible candidate when one was observed. The optimized aggregate is not the returned candidate's source objective.

## Class: `RecursiveQAOA`

```text
RecursiveQAOA(qaoa=QAOA(), min_variables=10, eliminations_per_step=1)
```

Each omitted `qaoa` creates a separate default QAOA configuration.

| Parameter | Type | Contract |
| --- | --- | --- |
| `qaoa` | `QAOA` | Inner algorithm; `initial_point`, `initial_state`, `warm_start`, and `mixer` must be `None`. Standard or multi-angle parameterization and CVaR are supported. |
| `min_variables` | `int` | Integer in `[1, 20]`, excluding Boolean values. The algorithm can reduce further to satisfy exact residual-work limits. |
| `eliminations_per_step` | `int` | Positive maximum number of disjoint pair relations selected per stage. |

`run(problem, *, execution=None, mode="local") -> QuantumResult` accepts explicit optimization QUBO/Ising input, including supported dictionaries. It rejects generic Hamiltonians. It requires the compiled local runtime and QCloud authentication in either mode. There is no static `build_circuit` method. The [recursive workflow](quantum-optimization.md#recursive-qaoa) explains reconstruction, zero-stage behavior, and the heuristic guarantee.

## Class: `ADAPTQAOA`

```text
ADAPTQAOA(operator_pool="single", optimizer="nelder_mead",
          max_iterations=100, max_layers=10,
          gradient_tolerance=1e-3, selection_gamma=0.01)
```

| Parameter | Type | Contract |
| --- | --- | --- |
| `operator_pool` | `str \| Sequence[Sequence[tuple[int, str]]]` | `"single"`, `"multi"`, or a non-empty sequence of distinct Pauli strings. Each string contains distinct non-negative qubit indices paired with X, Y, or Z. Indices must fit the problem register. |
| `optimizer` | `str \| Callable` | Named optimizer or the custom optimizer contract below. |
| `max_iterations` | `int` | Positive optimizer budget for each selected layer's joint optimization. |
| `max_layers` | `int` | Positive maximum number of committed mixer selections. |
| `gradient_tolerance` | `float` | Finite positive Euclidean gradient-norm stopping threshold. |
| `selection_gamma` | `float` | Finite positive cost-evolution angle used when evaluating pool gradients. |

`run(problem, *, execution=None, mode="local") -> QuantumResult` accepts the same quadratic input representations as QAOA. There is no static `build_circuit` method. CVaR, warm start, custom initial states, and XY mixers are not configuration fields. See the [ADAPT-QAOA mechanism](quantum-optimization.md#adapt-qaoa).

## Named and custom optimizers

Named optimizers are `"cobyla"`, `"spsa"`, `"nelder_mead"`, and `"powell"`. Names are case-insensitive and hyphens normalize to underscores. QAOA, SamplingVQE, and ADAPT-QAOA use the shared optimizer adapter; VQE delegates to the QpiAI Quantum SDK's VQE implementation.

QAOA and ADAPT-QAOA additionally accept:

```text
optimizer(objective, initial_parameters, max_iterations)
# Return (parameters, value) or (parameters, value, info).
```

`objective` accepts a one-dimensional finite NumPy parameter vector and returns a finite `float`; evaluating it can execute a circuit. `initial_parameters` is a detached one-dimensional NumPy array. Returned `parameters` must have the same length and finite real entries; `value` must be finite. Optional `info` is a mapping whose recognized diagnostics are `nfev` and `nit` (non-negative integral values or `None`), `history` (finite objective values), and `param_history` (finite vectors matching the parameter count). Missing histories default to empty lists and missing counts to `None`. Malformed diagnostics fail before final circuit execution. The callback owns its stopping behavior and must honor the caller's resource budget.

## Class: `QuantumRun`

```text
QuantumRun(*, shots=1024, experiment_name=None, timeout=None,
           seed=None, device_name=None)
```

| Field | Type | Contract |
| --- | --- | --- |
| `shots` | `int` | Strict positive integer per circuit execution, not a total variational-run budget. |
| `experiment_name` | `str \| None` | Non-empty stripped name. Platform omission uses `"Default Experiment"`; an explicit name must identify an existing experiment. |
| `timeout` | `float \| None` | Finite positive platform circuit timeout in seconds. Rejected locally. It is not a total algorithm deadline. |
| `seed` | `int \| None` | Strict integer in `[0, 2**32 - 1]`; local sampling, random initialization, and supported SPSA random calls. Rejected in platform mode. |
| `device_name` | `str \| None` | Non-empty stripped QCloud selection; omission uses `"QpiAI-Indus-1"` in platform mode. Rejected locally; `"QpiAI-QSV-Local"` is rejected in platform mode. |

Omitting the `execution` argument uses `QuantumRun()`. Execution accepts only `mode="local"` or `mode="platform"`. Gate-based optimization uses QCloud authentication and accepts execution settings through `QuantumRun`. Do not pass an Opt `Client` or solver options such as `time_limit` (an LP/MILP/QP time limit) or `num_reads` (a QUBO/HUBO/Ising sampling count). Select `mode="platform"` explicitly for QCloud execution; `mode="auto"` and `allow_platform=True` are rejected.

## Class: `CVaR`

```text
CVaR(*, alpha=1.0)
aggregate(distribution: Mapping[float, float]) -> float
```

`alpha` is finite and in `(0, 1]`. `distribution` is a non-empty energy-to-probability mapping; energies and probabilities must be finite non-Boolean real values, probabilities non-negative, and total probability close to one with relative tolerance `1e-10` and absolute tolerance `1e-12`. Accepted mass is normalized, zero-mass buckets are ignored, and the lowest-energy tail includes a fractional boundary bucket. An unrepresentable nonzero aggregate raises rather than silently underflowing. Invalid values raise `ValueError` or a Pydantic validation error during construction.

## Class: `WarmStart`

```text
WarmStart(*, epsilon=0.25, relaxed_solution=None)
```

`epsilon` is a finite real number in `[0, 0.5]`. `relaxed_solution` is `None`, a sequence, a complete variable-name mapping, or a mapping covering consecutive indices `0..n-1`; all values must lie in `[0, 1]`. Indexed mappings and sequences become tuples; name mappings become detached immutable mappings. Problem-dependent length and name coverage are checked during inspection or construction. `None` requests the bounded local continuous relaxation. Values are clipped to `[epsilon, 1-epsilon]` for state preparation and the tilted mixer. This does not establish a global relaxation optimum.

## Class: `XYMixer`

```text
XYMixer(*, groups, topology="ring")
edges() -> tuple[tuple[int, int], ...]
```

`groups` is a non-empty sequence of disjoint groups, each containing at least two distinct non-negative Python integer qubit indices; booleans are rejected. It is stored as a tuple of tuples. `topology` is `"ring"` or `"full"`, normalized to lowercase. `edges()` returns stable, unique pairs with each pair sorted. Groups need not cover the whole register; only the configured edges mix qubits. Register-range checks occur with a problem. The product of paired XX/YY rotations preserves each group's Hamming weight; overlapping edges need not commute.

## Function: `build_circuit`

```text
build_circuit(problem, *, algorithm, parameters=None,
              measurements=False) -> Circuit
```

`algorithm` must be `QAOA`, `VQE`, or `SamplingVQE`. `parameters` is a finite vector of the exact parameter count; `None` uses the algorithm's `initial_point`, which must then be supplied. `measurements` must be a strict Boolean. Returns a numeric `Circuit` without authentication or execution. Invalid algorithm types raise `TypeError`; adaptive algorithms and missing or incompatible parameters raise `ValueError`.

## Class: `QuantumSample`

Keyword-only Pydantic fields:

| Field | Type | Default and meaning |
| --- | --- | --- |
| `bitstring` | `str` | Required non-empty binary measurement string, most significant bit first. Its rightmost bit is qubit zero. |
| `count` | `int` | Required strict non-negative observation count. |
| `probability` | `float` | Required finite frequency in `[0, 1]` within this candidate distribution. |
| `internal_energy` | `float` | Required finite energy in the canonical minimizing representation, including its offset; it can differ from the restored source objective for compiled models. |
| `objective` | `float \| None` | `None`; restored source objective when evaluable. |
| `feasible` | `bool \| None` | `None`; source feasibility when known. |
| `solution` | `list[int \| float] \| None` | `None`; finite coordinates in the problem's binary or spin order. |
| `variable_assignment` | `dict[str, int \| float] \| None` | `None`; finite source-name coordinates when available. |

## Class: `LogicalCircuitInfo`

`LogicalCircuitInfo(*, depth, size, gate_counts)` requires strict non-negative integer `depth` and `size` and a `dict[str, int]` of non-empty gate names to strict non-negative counts. Metrics describe the logical circuit before physical routing and native-gate decomposition.

## Class: `QuantumDiagnostics`

Keyword-only Pydantic fields; unknown fields are rejected:

| Field | Type | Default and meaning |
| --- | --- | --- |
| `algorithm` | `str` | Required non-empty algorithm name. |
| `backend` | `str` | Required non-empty actual execution backend. |
| `shots` | `int` | Required strict positive shots per circuit. |
| `optimizer` | `str \| None` | `None`; non-empty name when known. |
| `evaluations`, `iterations` | `int \| None` | Each `None`; strict non-negative diagnostics when returned. |
| `optimized_parameters` | `list[float]` | `[]`; finite final parameter vector. |
| `objective_history` | `list[float]` | `[]`; finite aggregate or variational objective history, not necessarily source objectives. |
| `parameter_history` | `list[list[float]]` | `[]`; finite optimizer parameter vectors when returned. |
| `counts` | `dict[str, int]` | `{}`; binary strings to strict non-negative counts; zero entries are removed. |
| `candidates` | `list[QuantumSample]` | `[]`; observed candidate distribution. |
| `job_ids` | `list[str]` | `[]`; actual non-empty QCloud circuit job identifiers. |
| `logical_circuit` | `LogicalCircuitInfo \| None` | `None`; measured logical metrics when available. |
| `qpu_metadata` | `list[dict[str, JSONValue]]` | `[]`; metadata returned by the QpiAI Quantum SDK. |
| `transformations` | `list[dict[str, JSONValue]]` | `[]`; explicit mathematical representation records. |
| `algorithm_metadata` | `dict[str, JSONValue]` | `{}`; algorithm-specific diagnostics. |

`JSONValue` denotes JSON null, Boolean, integer, finite floating-point number, string, list of JSON values, or string-keyed object of JSON values. Non-finite numbers, non-string metadata keys, unsupported objects, and cycles are rejected. List and dictionary defaults are independent for every instance.

For QAOA, VQE, SamplingVQE, and ADAPT-QAOA, `counts` holds the final distribution. RecursiveQAOA holds reconstructed full-register observations across stages; its unsampled exact residual is recorded separately. Candidate counts can therefore exceed `shots`, and candidate probabilities need not match the final `counts` distribution. See [result semantics](quantum-optimization.md#results-and-automation).

### Algorithm metadata

Completed algorithms populate `algorithm_metadata` with the fields below. These fields describe the optimization process; the selected source assignment and its restored objective are in `QuantumResult`.

#### QAOA, VQE, and SamplingVQE

| Field | Algorithms | Type and meaning |
| --- | --- | --- |
| `reps` | QAOA | `int`; configured number of layers. |
| `parameterization` | QAOA | `str`; `"standard"` or `"multi_angle"`. |
| `parameter_order` | QAOA | `str`; `"gamma[0:p], beta[0:p]"` for standard parameters, or `"per layer: sorted Z terms, sorted ZZ terms, then mixer generators"` for multi-angle parameters. |
| `mixer` | QAOA | `str`; `"x"`, `"xy"`, `"warm_start"`, or `"custom"`. |
| `ansatz` | VQE, SamplingVQE | `str`; the supplied built-in name, or `"custom"` for a circuit or callable. |
| `aggregation` | QAOA, SamplingVQE | `"expectation"` for the mean, or the CVaR `alpha` as a `float`. |
| `optimized_aggregate` | QAOA, SamplingVQE | `float`; optimized mean or CVaR in the same energy coordinates as `objective_history`. |
| `variational_energy` | VQE | `float`; optimized Hamiltonian expectation in the same energy coordinates as `objective_history`. |
| `optimizer_message` | VQE | `str`; optimizer message, or `""` when unavailable. |
| `optimization_offset` | QAOA; optimization-origin VQE and SamplingVQE | `float`; constant omitted from optimizer energies, as defined below. Absent for generic-Hamiltonian VQE and SamplingVQE. |
| `candidate_distribution` | All three | `str`; `"all computational-basis measurements"` for VQE, or `"all variational and final measurement evaluations"` for QAOA and SamplingVQE. |

For these algorithms and ADAPT-QAOA, QUBO optimizer energies omit the input QUBO's constant and use the minimizing sign: `optimization_offset` is `problem.offset` for MIN and `-problem.offset` for MAX. Adding it to an optimizer energy restores the full canonical minimizing energy. Ising and generic Hamiltonian energies retain their constants; their offset field is zero when present. Compiled penalties remain part of these energies. A restored source objective can differ after source decoding and is reported separately.

QAOA with `WarmStart` adds:

| Field | Type and meaning |
| --- | --- |
| `relaxation_method` | `str`; `"L-BFGS-B box relaxation"` for automatic relaxation or `"supplied"` for a supplied solution. |
| `relaxation_global` | `False` for automatic relaxation; `None` for a supplied solution. |
| `relaxed_solution` | `list[float]`; binary probabilities before clipping, in variable order. |
| `clipped_solution` | `list[float]`; probabilities clipped to `[epsilon, 1 - epsilon]`. |
| `relaxation_success` | `bool`; automatic optimizer's success flag. |
| `relaxation_objective` | `float`; automatic box-relaxation objective. |
| `relaxation_offset` | `float`; add to `relaxation_objective` to obtain the full minimizing Ising energy at the relaxed point. |
| `relaxation_message` | `str`; automatic optimizer's termination message. |

The last four fields are present only for automatic relaxation. Its success flag records optimizer termination; it does not establish a global relaxation optimum.

#### ADAPT-QAOA

| Field | Type and meaning |
| --- | --- |
| `operator_pool` | `str`; `"single"`, `"multi"`, or `"custom"`. |
| `operator_pool_size` | `int`; number of operators in the pool. |
| `operator_sequence` | `list[str]`; selected operator labels in layer order. Labels concatenate Pauli letters and qubit indices; sums separate terms with `" + "`. |
| `layers` | `int`; number of selected layers. |
| `selection_gamma`, `gradient_tolerance` | `float`; configured selection angle and stopping threshold. |
| `final_gradient_norm` | `float`; Euclidean norm of the last evaluated pool gradient. |
| `selection_history` | `list[dict]`; one record per selected layer, as defined below. |
| `optimization_offset` | `float`; constant adjustment defined above. |
| `candidate_distribution` | `str`; `"all variational and final measurement evaluations"`. |

Each selection record contains `layer: int` (zero-based), `gradient_norm: float`, `selected_operator: str`, and `selected_gradient: float`. The selected gradient retains its sign. `objective_history` contains mean energies from variational objective evaluations.

#### RecursiveQAOA

| Field | Type and meaning |
| --- | --- |
| `reductions` | `list[dict]`; reduction-stage records, as defined below. |
| `quantum_stages` | `int`; number of QAOA reduction stages. |
| `quantum_execution_performed` | `bool`; whether any QAOA stage ran. |
| `residual_variables` | `int`; size of the final reduced register. |
| `residual_solver` | `str`; `"qpiai-opt local exact Ising"`. |
| `residual_objective` | `float`; exact objective of the reduced Ising problem. |
| `optimization_offset` | `float`; initial minimizing Ising constant after binary-to-spin conversion for QUBO input, or zero for Ising input. |
| `reconstructed_internal_energy` | `float`; full original canonical minimizing energy of the reconstructed residual candidate. |
| `residual_candidate` | `dict`; `bitstring: str` in the full register, restored `objective: float`, and source `feasible: bool`. This candidate need not be selected as the top-level result. |
| `candidate_distribution` | `str`; `"reconstructed full-register samples from all QAOA stages"`. |

Each reduction record contains `input_variables: int`, `output_variables: int`, and `relations: list[dict]`. Its `objective_history_range: list[int]` and `parameter_history_range: list[int]` are half-open `[start, end]` slices into the corresponding concatenated histories. Each relation contains original-register indices `eliminated: int` and `retained: int`, `sign: int` in `{-1, 1}`, `correlation: float` in `[-1, 1]`, and `absolute_correlation: float`. The relation is `s_eliminated = sign * s_retained`.

Histories use each stage's reduced problem. For QUBO input, reduction constants are omitted from stage and residual objectives; the single `optimization_offset` does not restore every stage to original-problem energies. Use candidate `internal_energy`, `reconstructed_internal_energy`, or the restored source `objective` for comparisons in the original problem's coordinates.

## Class: `QuantumResult`

Keyword-only fields:

| Field | Type | Default and meaning |
| --- | --- | --- |
| `problem_class` | `Literal["QUBO", "ISING", "QUBIT_HAMILTONIAN"]` | Required mathematical result class. |
| `execution_mode` | `Literal["local", "platform"]` | Required execution mode. |
| `quantum` | `QuantumDiagnostics` | Required algorithm diagnostics. |
| `objective` | `float \| None` | `None`; finite source objective for an optimization candidate. |
| `solution` | `list[int \| float] \| None` | `None`; binary QUBO or spin Ising vector. |
| `variable_assignment` | `dict[str, int \| float] \| None` | `None`; finite source assignment, including decoding when retained. |
| `eigenvalue` | `float \| None` | `None`; required finite generic Hamiltonian estimate. |
| `solve_info` | `QuantumSolveInfo \| None` | `None` for manual construction; attached by completed SDK algorithms. |
| `job_id`, `problem_id` | `str \| None` | Each `None`; the composite algorithm has no optimization-platform job. Circuit IDs are in diagnostics. |
| `solver_time_s`, `compute_time_s` | `float \| None` | Each `None`; finite non-negative durations in seconds when measured. |

For optimization problems, gate-based methods return the best source-feasible assignment seen across the run, according to the source MIN/MAX objective. Selection uses objective value, not observation count. RecursiveQAOA also considers the reconstructed exact-residual candidate.

An optimization result supplies `objective`, `solution`, and `variable_assignment` together, or leaves all three `None` when no usable candidate was observed. It cannot contain `eigenvalue`. A generic Hamiltonian result requires `eigenvalue` and forbids those three fields. QUBO vectors contain only 0/1; Ising vectors contain only -1/+1. Source assignments may contain other finite values after decoding.

Properties `counts`, `parameters`, and `history` return the corresponding diagnostics containers. `info() -> QuantumSolveInfo` returns a detached deep copy; manually constructed results without information raise `RuntimeError`. `runtime(unit="s", *, kind="compute") -> float | None` accepts units `"s"`, `"ms"`, or `"us"` and kinds `"compute"` or `"solver"`; unavailable values stay `None`. `compute_time_ms`, `compute_time_us`, `solver_time_ms`, and `solver_time_us` are equivalent unit views. Invalid units, kinds, or unrepresentable conversions raise `ValueError`.

## Class: `QuantumSolveInfo`

This keyword-only Pydantic model inherits the [common solve information fields](local-solving.md#common-solve-information) and specializes:

| Field | Type | Contract |
| --- | --- | --- |
| `problem_class` | `Literal["QUBO", "ISING", "QUBIT_HAMILTONIAN"]` | Required. |
| `execution_mode` | `Literal["local", "platform"]` | Required. |
| `termination` | `Literal["completed", "limit_reached", "failed"]` | Required; never claims an optimal termination. |
| `optimality_proven` | `Literal[False]` | Defaults to `False`. |
| `guarantee` | `Literal["best_found", "none", "unknown"]` | Required; usable optimization candidates use `"best_found"`. |
| `transformed` | `bool` | Required transformation indicator. |

Inherited fields are `provider: str`, `solver: str | None = None`, `method: str | None = None`, required `has_usable_solution: bool` and `solution_feasible: bool | None`, `source_objective: float | None = None`, and `settings: dict[str, int | float | str | bool] = {}`. Generic Hamiltonian estimates cannot claim a usable variable assignment. Frozen field assignment does not make nested dictionaries immutable.

## QpiAI Quantum SDK types

`Circuit`, `QubitHamiltonian`, and `QpiAIQuantumAuth` are re-exported from `qpiai-quantum`. Their circuit algebra, drawing, execution, Hamiltonian operations, and authentication API are documented by the [QpiAI Quantum SDK](https://docs.qcloud.qpiai.tech/) and its [source repository](https://github.com/qpiai/quantum-sdk). Use optimization `build_circuit` and `solve` for optimization workflows, and the QpiAI Quantum SDK for general circuit and QCloud operations.

Obtain a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/). The optimization workflow uses `QpiAIQuantumAuth.login(api_key=...)`, and optionally `QpiAIQuantumAuth.list_compute_resources(display=False)` for a read-only account snapshot. Resource discovery does not select or validate `QuantumRun.device_name`. `QubitHamiltonian` accepts terms of the form `[(operators, coefficient), ...]`, where `operators` is a sequence of `(qubit_index, "X" | "Y" | "Z")` pairs and an empty sequence represents identity. Optimization conversion preserves register and source metadata; do not discard it when isolated qubits or source coordinates matter.
