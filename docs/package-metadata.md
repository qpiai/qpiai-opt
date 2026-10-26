# Package metadata and system support

| Field | Value |
| --- | --- |
| Distribution | `qpiai-opt` |
| Import package | `qpiai_opt` |
| Version | `0.0.9` |
| License | Apache-2.0 |
| Python | CPython 3.10–3.14 |
| Operating systems | Linux, macOS, Windows |
| Dependency requirements | Declared in [pyproject.toml](../pyproject.toml); installed automatically with the package |
| Optional integrations | Gate-based optimization through `qpiai-opt[quantum]` (QpiAI Quantum SDK 0.3.0); OMMX 2.6–2.x through `qpiai-opt[ommx]`; both through `qpiai-opt[all]` |
| Platform API root | `https://opt-server.qpiai.tech/api` |
| Local access | [Free registration and an Opt key](https://opt.qpiai.tech/), verified online before local solving with `Client` |
| Local gate-based optimization access | [Free QCloud API key](https://qcloud.qpiai.tech/), authenticated and verified online before execution |
| Local runtime distribution | Binary-only native extension in CPython/platform-specific wheels |

The base package includes local LP, MILP, QP, QUBO, HUBO, and Ising solvers. The public modelling and interoperability SDK is open source; the local solver runtime is distributed in binary-only form under Apache-2.0. Standard pip installation from PyPI selects the compatible wheel and installs its dependencies. Building a complete SDK wheel from the [public source repository](https://github.com/qpiai/qpiai-opt) requires a compatible native binary. See [Contributing](../CONTRIBUTING.md) for source development and documentation setup. Modelling, inspection, validation, conversion, and result evaluation remain credential-free. Local authentication does not create jobs or consume credits.

The `quantum` extra adds the pinned `qpiai-quantum==0.3.0` integration; `all` includes it and OMMX. Local simulation and circuit execution use `qpiai-quantum`. Optional dependencies follow their own wheel availability.

Canonical IR uses schema version `qpiai-opt-ir/1.0`, independently of the package version.
