"""Gate-based optimization integrated with :mod:`qpiai_opt`.

The optional dependency is imported only when a quantum type is requested;
install it with ``pip install 'qpiai-opt[quantum]'``.
"""

from .core import (
    ADAPTQAOA,
    QAOA,
    VQE,
    CVaR,
    QuantumDiagnostics,
    QuantumResult,
    QuantumRun,
    QuantumSample,
    QuantumSolveInfo,
    RecursiveQAOA,
    SamplingVQE,
    WarmStart,
    XYMixer,
    build_circuit,
)
from .types import LogicalCircuitInfo

try:
    from qpiai_quantum import Circuit, QpiAIQuantumAuth
    from qpiai_quantum.applications.matter.models import QubitHamiltonian
except ImportError as exc:  # pragma: no cover - optional dependency path
    _quantum_import_error = exc

    def _missing_quantum(*args, **kwargs):
        raise ImportError(
            "Gate-based optimization requires the optional 'qpiai-quantum' dependency. "
            "Install it with: pip install 'qpiai-opt[quantum]'"
        ) from _quantum_import_error

    Circuit = QubitHamiltonian = QpiAIQuantumAuth = _missing_quantum

__all__ = [
    "QAOA",
    "VQE",
    "SamplingVQE",
    "RecursiveQAOA",
    "ADAPTQAOA",
    "WarmStart",
    "CVaR",
    "XYMixer",
    "QuantumRun",
    "QuantumResult",
    "QuantumDiagnostics",
    "LogicalCircuitInfo",
    "QuantumSample",
    "QuantumSolveInfo",
    "QubitHamiltonian",
    "Circuit",
    "QpiAIQuantumAuth",
    "build_circuit",
]
