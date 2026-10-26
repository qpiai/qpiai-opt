"""Optimization modelling, conversion, and interchange interfaces."""

from .constraints import (
    AND,
    NOT,
    OR,
    SOS1,
    SOS2,
    XOR,
    CustomPenalty,
    Indicator,
)
from .converters import (
    HUBOToIsingConvert,
    HUBOToQUBOConvert,
    IsingToQUBOConvert,
    MILPToQUBOConvert,
    QUBOToIsingConvert,
    QUBOToMILPConvert,
    hubo_to_ising,
    hubo_to_qubo,
    ising_to_qubo,
    milp_to_qubo,
    qubo_to_ising,
    qubo_to_milp,
)
from .encoders import encode_continuous, encode_fraction, encode_integer_binary
from .expressions import Expression, Term, dot, quicksum
from .indexed import IndexedVariables
from .ir import IR_VERSION, dump, dumps, from_ir, load, loads, to_ir
from .model import Model
from .ommx import from_ommx, to_ommx
from .parameters import Parameter
from .piecewise import PiecewiseLinear
from .variables import (
    Binary,
    Continuous,
    Fraction,
    Integer,
    Mixed,
    SemiContinuous,
    SemiInteger,
    Spin,
    Variable,
    create_binary_array,
    create_spin_array,
)

__all__ = [
    # Core
    "Model",
    "to_ommx",
    "from_ommx",
    "Expression",
    "Term",
    "Parameter",
    "PiecewiseLinear",
    "IndexedVariables",
    "Binary",
    "Spin",
    "Integer",
    "Continuous",
    "SemiContinuous",
    "SemiInteger",
    "Fraction",
    "Mixed",
    "create_binary_array",
    "create_spin_array",
    "Variable",
    "quicksum",
    "dot",
    "IR_VERSION",
    "to_ir",
    "from_ir",
    "dumps",
    "loads",
    "dump",
    "load",
    # Constraints
    "SOS1",
    "SOS2",
    "Indicator",
    "AND",
    "OR",
    "XOR",
    "NOT",
    "CustomPenalty",
    # QUBO utilities
    "encode_integer_binary",
    "encode_continuous",
    "encode_fraction",
    "hubo_to_qubo",
    "hubo_to_ising",
    "HUBOToIsingConvert",
    "qubo_to_milp",
    "milp_to_qubo",
    "qubo_to_ising",
    "ising_to_qubo",
    "HUBOToQUBOConvert",
    "QUBOToMILPConvert",
    "MILPToQUBOConvert",
    "IsingToQUBOConvert",
    "QUBOToIsingConvert",
]
