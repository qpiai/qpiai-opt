# Gate-based optimization

Install the optional integration with `pip install "qpiai-opt[quantum]"`. All supported optimization-facing objects are imported from `qpiai_opt.quantum`; `Circuit`, `QubitHamiltonian`, and `QpiAIQuantumAuth` are re-exported from `qpiai-quantum==0.3.0`.

The [gate-based optimization API reference](api-quantum.md) lists constructors, parameters, methods, result fields, and validation rules.

Obtain a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/). The login code reads it from the application's `QPIAI_QCLOUD_API_KEY` environment variable. This explicit Python lookup is independent of the Opt `Client` credential.

```python
import os

from qpiai_opt.opt_modeller import Binary, Model
from qpiai_opt.opt_solvers import solve
from qpiai_opt.opt_solvers.qubo import QUBOProblem
from qpiai_opt.quantum import QAOA, QpiAIQuantumAuth, QuantumRun

left, right = Binary("left"), Binary("right")
model = Model("quantum_selection")
model.minimize(-3 * left - 2 * right + 4 * left * right)
qubo = QUBOProblem.from_model(model)

QpiAIQuantumAuth.login(api_key=os.environ["QPIAI_QCLOUD_API_KEY"])

result = solve(
    qubo,
    algorithm=QAOA(reps=2),
    execution=QuantumRun(shots=1024, seed=7),
)
```

`mode="local"` uses the QpiAI Quantum SDK's in-process statevector simulator. `mode="platform"` explicitly executes the variational circuits through QCloud. Gate-based optimization calls reject `mode="auto"`, so local limits never trigger unrequested QCloud spending. `QuantumRun.timeout` and `QuantumRun.device_name` are platform-only, and `QuantumRun.seed` is local-only; the seed controls local sampling and seeded SPSA calls.

Every local gate-based optimization solve verifies the active QCloud credential online before computation, including direct algorithm `run()` calls. An internet connection is required; stored login state does not replace fresh verification. Authentication failures stop execution. Circuit execution and simulation use `qpiai-quantum`. Circuit construction, inspection, and validation remain credential-free.

Gate-based optimization uses `QpiAIQuantumAuth` for QCloud authentication and account resource listing. The Opt `Client` is rejected for gate-based optimization calls. Resource listing returns account information without executing circuits:

```python
import os

from qpiai_opt.quantum import QAOA, QpiAIQuantumAuth, QuantumRun

QpiAIQuantumAuth.login(api_key=os.environ["QPIAI_QCLOUD_API_KEY"])
resources = QpiAIQuantumAuth.list_compute_resources(display=False)

for resource in resources:
    print(resource["backend_name"], resource["device_name"], resource["usage_rate"])
```

Choose execution independently. Omit `device_name` for the `QpiAI-Indus-1` default, or supply a supported device name chosen by the user or application:

```python
selected_device = "QpiAI-QDM-Simulator"

result = solve(
    qubo,
    mode="platform",
    algorithm=QAOA(reps=3, optimizer="spsa", max_iterations=150),
    execution=QuantumRun(
        shots=4096,
        experiment_name="qaoa-study",
        device_name=selected_device,
    ),
)
```

Set `QuantumRun.device_name` directly; it defaults to `QpiAI-Indus-1`. The QpiAI Quantum SDK validates device names, and QCloud checks access and availability during execution. `list_compute_resources()` returns an account snapshot and does not select the device. `QpiAI-QSV-Local` is rejected in platform mode; local execution uses `mode="local"` without `device_name`. `result.quantum.backend` records the selected backend.

Gate-based optimization is a blocking `solve()` workflow because one variational run orchestrates many circuit executions. The optimization SDK's `submit()`, `status()`, `result()`, and `cancel()` operations do not represent that composite lifecycle.

## QAOA

The quantum approximate optimization algorithm was introduced by [Farhi, Goldstone, and Gutmann](https://arxiv.org/abs/1411.4028). The following conventions define this SDK's implementation.

`QAOA` accepts explicit QUBO, Ising, and exactly convertible identity/Z/ZZ `QubitHamiltonian` inputs. It does not implicitly transform `Model`, LP, MILP, QP, or HUBO input; convert first so penalties, auxiliaries, transformations, and qubit count remain visible.

Standard parameterization uses exactly `2 * reps` values ordered as `gamma[0:reps]` followed by `beta[0:reps]`. With $s=2x-1$ and computational Pauli eigenvalue $z=1-2x=-s$, the SDK maps

```latex
$$
E(s)=c+\sum_i h_i s_i+\sum_{i<j}J_{ij}s_i s_j
\quad\longrightarrow\quad
H=cI-\sum_i h_i Z_i+\sum_{i<j}J_{ij}Z_iZ_j.
$$
```

For $U_C(\gamma)=e^{-i\gamma H}$ and $U_M(\beta)=e^{-i\beta\sum_iX_i}$, each layer applies `RZ(-2 * gamma * h_i)`, `RZZ(2 * gamma * J_ij)`, and `RX(2 * beta)`. The constant contributes only a global phase and is retained in sampled energy evaluation rather than emitted as a gate.

```python
algorithm = QAOA(
    reps=2,
    parameterization="standard",
    optimizer="cobyla",
    max_iterations=100,
)
```

The supported named optimizers are `"cobyla"`, `"spsa"`, `"nelder_mead"`, and `"powell"`; these call the pinned QpiAI Quantum SDK implementations. A QAOA custom optimizer receives `(objective, initial_parameters, max_iterations)` and returns `(parameters, value)` or `(parameters, value, info)`.

### Multi-angle QAOA

`parameterization="multi_angle"` gives every cost generator and mixer generator an independent angle in each layer. The stable order is, per layer: increasing-qubit Z fields, lexicographically ordered ZZ couplings, then increasing-qubit X or warm-start generators; an `XYMixer` instead uses its stable edge order. Each XY exchange generator shares one angle across its paired XX and YY rotations.

```python
algorithm = QAOA(reps=2, parameterization="multi_angle")
```

### CVaR

The [CVaR aggregation approach of Barkoutsos et al.](https://arxiv.org/abs/1907.04769) replaces the sampled mean with a lower-tail objective.

`CVaR(alpha=alpha)` averages the lowest canonical minimization-energy probability mass of size $\alpha$, including a fractional boundary bucket when needed. `alpha=1` is ordinary expectation aggregation. CVaR applies to QAOA, SamplingVQE, and the QAOA stages inside RecursiveQAOA. Generic estimator VQE does not expose it, and ADAPT-QAOA retains the expected-energy objective required by its commutator-gradient selection rule.

```python
from qpiai_opt.quantum import CVaR

algorithm = QAOA(aggregation=CVaR(alpha=0.25))
```

### Warm start

Warm-start QAOA prepares the variational state from a continuous relaxation; see [Egger, Marecek, and Woerner](https://arxiv.org/abs/2009.10095).

`WarmStart` accepts a relaxed binary vector in $[0,1]^n$ or computes a bounded continuous relaxation with SciPy L-BFGS-B. That default relaxation is local and nonconvex; diagnostics report its method, objective, status, and `relaxation_global=False`. Values are clipped to $[\epsilon,1-\epsilon]$, then qubit $i$ is prepared with $R_Y(\theta_i)$ where $\theta_i=2\arcsin\sqrt{c_i}$.

The implemented tilted mixer is $H_M=\sum_i(\sin\theta_i X_i+\cos\theta_i Z_i)$, which differs from the negative-Hamiltonian convention in the warm-start literature only by $\beta\mapsto-\beta$. Its circuit sequence is `RY(-theta_i)`, `RZ(2 * beta_i)`, `RY(theta_i)`. The prepared state is an eigenstate of this mixer, and `epsilon=0.5` recovers the standard X-mixer circuit exactly.

```python
from qpiai_opt.quantum import WarmStart

algorithm = QAOA(
    reps=2,
    parameterization="multi_angle",
    warm_start=WarmStart(epsilon=0.25),
    aggregation=CVaR(alpha=0.2),
)
```

Warm start cannot be combined with an explicit initial state, `XYMixer`, or custom mixer because those choices define conflicting preparation or mixing semantics.

### Fixed-Hamming-weight and one-hot mixing

`XYMixer` applies an ordered product of XX and YY exchange rotations on each disjoint group. Every exchange preserves that group's Hamming weight, so a one-hot initial state remains one-hot. `topology="ring"` and `topology="full"` are supported. Rotations on overlapping edges do not all commute; the mixer unitary follows the specified product order.

```python
from qpiai_opt.quantum import XYMixer

algorithm = QAOA(
    initial_state=[1, 0, 0, 0],
    mixer=XYMixer(groups=[[0, 1, 2, 3]], topology="ring"),
)
```

Groups must be disjoint and contain at least two valid qubit indices. The SDK validates the supplied basis-state length and values but does not infer arbitrary source constraints or synthesize mixers for them. A custom initial-state `Circuit` may be supplied without measurements. A custom mixer callable receives `(circuit, beta, layer)` and must mutate that same circuit; it may return `None` or the same circuit. Custom mixers use standard shared-angle parameterization because their independent generator count cannot be inferred safely.

## VQE and SamplingVQE

`VQE` uses `qpiai_quantum.VQESolver` for ansatz construction, Pauli grouping, basis-rotation measurement, and variational optimization. Built-in ansätze are `"standard"`, `"hardware_efficient"`, and `"two_local"`; a `Circuit` or callable ansatz is also accepted.

```python
from qpiai_opt.quantum import QubitHamiltonian, VQE

hamiltonian = QubitHamiltonian(
    [
        ([(0, "Z")], -1.0),
        ([(1, "X")], 0.7),
        ([(0, "X"), (1, "X")], 0.2),
    ]
)
result = solve(hamiltonian, algorithm=VQE(ansatz="hardware_efficient"))
```

VQE on a generic `QubitHamiltonian` returns an eigenvalue estimate. For QUBO or Ising input, it converts through the canonical Hamiltonian map, evaluates measured bitstrings using source-model metadata when present, and returns the best observed source-feasible candidate. The variational energy is recorded in `result.quantum.algorithm_metadata["variational_energy"]`; the top-level `eigenvalue` field is reserved for generic Hamiltonian results.

`SamplingVQE` uses the same QpiAI Quantum SDK ansatz machinery but optimizes expectation or CVaR from computational-basis samples. It accepts explicit QUBO, Ising, and diagonal Pauli Hamiltonians of any locality. It rejects every X or Y term because a basis sample does not define that term's objective value.

```python
from qpiai_opt.quantum import SamplingVQE

result = solve(
    qubo,
    algorithm=SamplingVQE(
        ansatz="hardware_efficient",
        optimizer="spsa",
        aggregation=CVaR(alpha=0.25),
    ),
)
```

## Recursive QAOA

RecursiveQAOA requires an active QCloud credential and a compatible native local runtime in either execution mode. No Opt `Client` is required, including for a zero-stage local run that consists entirely of the exact residual. A missing runtime prevents the algorithm from starting, including before any platform circuit submission.

`RecursiveQAOA` repeatedly solves the current Ising problem, estimates pair correlations from the final optimized-stage samples, selects the strongest disjoint relations $s_i=r s_j$, substitutes them exactly, and records the complete reconstruction chain. It continues through zero correlations rather than stopping with an oversized residual. Reduction stops only when the residual is at or below `min_variables` and satisfies both the existing local exact solver's active-variable and enumeration-work limits; it may therefore reduce below the requested threshold. The residual is solved through that exact Ising route before the full source assignment is reconstructed.

```python
from qpiai_opt.quantum import RecursiveQAOA

result = solve(
    qubo,
    algorithm=RecursiveQAOA(
        qaoa=QAOA(reps=1, optimizer="spsa"),
        min_variables=10,
        eliminations_per_step=1,
    ),
)
```

The inner QAOA must use its default initial state, X mixer, and stage-local initial parameters because register size changes after every reduction. Multi-angle parameterization and CVaR remain valid. The exact residual solve does not prove the original optimum because the preceding correlation relations are heuristic; the final guarantee remains `best_found`.

If the initial problem already satisfies the exact-residual threshold, local execution performs no QAOA stage and diagnostics name the `qpiai-opt` exact Ising backend explicitly. A zero-stage platform request is rejected because it would execute no QCloud circuits. Lower `min_variables` when a QAOA reduction stage is intended.

## ADAPT-QAOA

`ADAPTQAOA` implements the iterative mechanism of [Zhu et al.](https://arxiv.org/abs/2005.10258). At layer $k$, it appends a trial cost evolution with `selection_gamma` (default `0.01`), evaluates each component $-i\langle\psi|e^{i\gamma H}[H,A_j]e^{-i\gamma H}|\psi\rangle$, stops when the Euclidean gradient norm is below `gradient_tolerance`, otherwise appends the largest-magnitude operator, and jointly optimizes every accumulated cost and mixer angle.

The `"single"` pool contains each $X_i$, each $Y_i$, $\sum_iX_i$, and $\sum_iY_i$. The `"multi"` pool adds every two-qubit Pauli product $B_iC_j$ for $B,C\in\{X,Y,Z\}$ and $i<j$. A custom pool is a sequence of Pauli strings such as `[[(0, "X"), (1, "Y")], [(2, "Z")]]`. The local selector evaluates commutator expectations exactly from the QpiAI Quantum SDK statevector simulator; platform selection groups compatible Pauli measurements and estimates them from QPU samples.

```python
from qpiai_opt.quantum import ADAPTQAOA

result = solve(
    qubo,
    algorithm=ADAPTQAOA(
        operator_pool="single",
        optimizer="nelder_mead",
        max_layers=10,
        gradient_tolerance=1e-3,
    ),
)
```

ADAPT-QAOA and RecursiveQAOA have no single static pre-run circuit. Their final/stage circuit metrics and adaptive metadata are available only after execution.

## Hamiltonian conversion and circuit inspection

`convert(problem, "QUBIT_HAMILTONIAN")` accepts QUBO or Ising input and preserves isolated variables through explicit Hamiltonian metadata. Reverse conversion to Ising accepts only real identity, Z, and ZZ terms; X, Y, repeated-qubit terms, locality above two, nonfinite coefficients, and malformed operators fail explicitly.

```python
from qpiai_opt.opt_solvers import convert, inspect

ising = convert(qubo, "ISING")
hamiltonian = convert(ising, "QUBIT_HAMILTONIAN")
restored = convert(hamiltonian, "ISING")
report = inspect(ising, algorithm=QAOA(reps=2))
```

`build_circuit()` constructs only a numeric logical circuit. It never authenticates, executes, or submits a job. Explicit parameters or an algorithm `initial_point` are required because the supplied circuit type is numeric rather than symbolically bindable.

```python
from qpiai_opt.quantum import build_circuit

circuit = build_circuit(
    qubo,
    algorithm=QAOA(reps=2),
    parameters=[0.1, 0.2, 0.3, 0.4],
)
print(circuit.depth())
print(circuit.size())
print(circuit.list_gates())
print(circuit.to_qasm())
circuit.show()
```

`inspect(problem, algorithm=...)` reports qubits, Hamiltonian term counts and locality, diagonal status, compatibility, parameter count, mixer, aggregation, ansatz or adaptive-pool settings, and logical circuit metrics when a static circuit exists. This includes a zero-bound numeric ansatz circuit for VQE and SamplingVQE inspection. Circuit depth, size, and gate counts are logical pre-routing metrics, not native physical-QPU metrics.

## Results and automation

Gate-based optimization algorithms return the Pydantic `QuantumResult` type. Optimization-origin results use normal `objective`, `solution`, and `variable_assignment` fields when a source-feasible candidate is available; QUBO solutions are binary and Ising solutions are spins. Candidate selection compares the restored source objective in its MIN/MAX direction across source-feasible observations from variational and final measurements. RecursiveQAOA also considers its reconstructed exact-residual candidate. The selected candidate need not be the modal final bitstring or the sample with lowest compiled penalty energy. `result.info()` returns `QuantumSolveInfo`; heuristic methods always report `optimality_proven=False` and never upgrade a sample to a proof.

`result.quantum` is a validated `QuantumDiagnostics` object containing the backend, shots, optimizer evaluations and iterations, optimized parameters, objective and parameter histories, counts, accumulated `QuantumSample` candidates, QCloud job identifiers and metadata when returned, logical circuit metrics, explicit representation transformations, and algorithm-specific metadata. Candidate probabilities describe the observations represented by that diagnostics object. For QAOA, VQE, SamplingVQE, and ADAPT-QAOA, `quantum.counts` is the final circuit distribution. For RecursiveQAOA it aggregates full-register samples reconstructed from every QAOA stage; the exact residual candidate is recorded separately in `algorithm_metadata` because it was not sampled.

Optimizer histories contain variational or aggregate energies. Their constants, minimizing sign, and compiled penalties can differ from the restored source objective. RecursiveQAOA histories also span different reduced problems. The [algorithm metadata reference](api-quantum.md#algorithm-metadata) defines the energy offsets, reduction records, and diagnostic fields for each algorithm.

```python
diagnostics = result.quantum
print(result.objective, result.variable_assignment)
print(diagnostics.optimized_parameters)
print(diagnostics.counts)
print(result.info().model_dump(mode="json"))
```

`schemas()` includes `quantum_run`, `quantum_cvar`, `quantum_warm_start`, `quantum_xy_mixer`, `quantum_sample`, `quantum_logical_circuit`, `quantum_diagnostics`, `quantum_result`, and `quantum_solve_info`. The `solve_result` union includes `QuantumResult` for result validation across execution routes. Credentials, authentication state, and circuit implementations remain in the executing application. Gate-based optimization uses blocking `solve()`; optimization `submit`, `status`, `result`, and `cancel` manage platform jobs for calls without `algorithm`.

Gate-based optimization results use the common `solver_time_s` and `compute_time_s` fields. Here the solver duration is elapsed time in the SDK's variational/adaptive algorithm, including its circuit evaluations; compute time also includes input preparation and result construction. This is a multi-circuit workflow: actual QCloud job identifiers remain in `quantum.job_ids`; the common `job_id` and `problem_id` fields are `None` because there is no composite optimization-platform job. `solve_info` retains `QuantumSolveInfo` in ordinary Python and JSON dumps.

`model.compile(target="ISING")` returns the native `IsingProblem` accepted by gate-based optimization routing and retains source decoding. `model.to_ising_dict()` and `convert(problem, "ISING")` return the equivalent mathematical dictionary; gate-based optimization circuit construction and execution accept that documented dictionary form too. `convert(hubo_problem, "QUBIT_HAMILTONIAN")` preserves higher-locality Z products. QAOA-family algorithms still require identity/Z/ZZ inputs; SamplingVQE supports diagonal higher-locality terms, while generic VQE also supports X/Y terms. Hamiltonian IR preserves real Pauli terms, register size, names, and source-sense metadata.
