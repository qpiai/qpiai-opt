# Getting started

## Installation

QpiAI Opt supports CPython 3.10–3.14 on Linux, macOS, and Windows.

```bash
python -m pip install qpiai-opt
```

The base install can solve LP, MILP, continuous QP, QUBO, HUBO, and Ising problems locally. Optional integrations are installed separately:

```bash
python -m pip install "qpiai-opt[quantum]"
python -m pip install "qpiai-opt[ommx]"
python -m pip install "qpiai-opt[all]"
```

The `quantum` extra installs the pinned `qpiai-quantum==0.3.0` integration; `ommx` installs the OMMX bridge; `all` installs both. Gate-based optimization requires QCloud authentication. Obtain a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/). Installing the extra does not change default solve routing. Optional packages follow their own wheel support. See [package support](package-metadata.md).

## Build a first model

Create variables, a model, an objective, and constraints. Variables are instances: use `Continuous("x", 0, 10)`, not the `Continuous` class itself.

```python
from qpiai_opt.opt_modeller import Continuous, Model

x = Continuous("x", 0, 4)
y = Continuous("y", 0, 3)

model = Model("production")
model.maximize(3 * x + 2 * y + 5)
model.add_constraint(x + y <= 5, label="capacity")
```

## Inspect before solving

`summary()` is for people. `classify()` and `inspect()` return deterministic programmatic descriptions.

```python
from qpiai_opt.opt_solvers import classify, inspect

human_summary = model.summary()
problem_class = classify(model)  # "LP"
report = inspect(model)
inspection_valid = report["valid"]
```

`human_summary` contains the objective, domains, and named constraints. `inspection_valid is True` for this valid small LP.

## Solve locally

Local execution is the default and cannot consume platform credits. Register for a free Opt API key at [QpiAI Opt](https://opt.qpiai.tech/) and supply it through `Client`; each solve verifies it online before computing locally. The code below reads the key explicitly from the application's `QPIAI_OPT_API_KEY` environment variable. A disconnected or rejected verification stops execution. Inspection and modelling do not need credentials.

```python
import os

from qpiai_opt.opt_solvers import Client, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])

result = solve(model, client=client)
result.problem_class  # "LP"
result.execution_mode  # "local"
result.status  # "optimal"
result.objective  # 19.0, including the source constant and MAX sense
result.solution  # {"x": 4.0, "y": 1.0}
result.compute_time_s  # default runtime unit
result.runtime("ms")  # explicit millisecond view
result.runtime("us")  # explicit microsecond view

info = result.info()
info.method  # "linear-programming"
info.termination  # "optimal"
info.guarantee  # "global_optimum"
```

Results identify the problem class and execution mode, with `None` job/problem IDs for local execution. Durations are stored in seconds; `runtime()` provides millisecond and microsecond views. The `solve_info` field serializes the method and guarantee returned by `info()`. See the [result contract](local-solving.md#completed-result-contract) for all fields and timing definitions.

## Running a job on platform

Supplying credentials alone does not submit work. Choosing platform mode does.

```python
import os

from qpiai_opt.opt_solvers import Client, solve

client = Client(api_key=os.environ["QPIAI_OPT_API_KEY"])
platform_result = solve(model, mode="platform", client=client)
```

Selecting platform mode with an authenticated client authorizes submission. Continue with [local and platform routing](local-solving.md), then the [completed result contract](local-solving.md#completed-result-contract).
