"""Versioned JSON-safe intermediate representation for SDK models."""

from __future__ import annotations

import json
import math
import numbers
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .._numeric import is_real_or_infinity
from .constraints import (
    AND,
    NOT,
    OR,
    SOS1,
    SOS2,
    XOR,
    CustomPenalty,
    Equal,
    GreaterEqual,
    Indicator,
    LessEqual,
)
from .expressions import Expression, Term, quicksum
from .model import Model
from .variables import (
    Binary,
    Continuous,
    Fraction,
    Integer,
    Mixed,
    SemiContinuous,
    SemiInteger,
    Spin,
)

IR_VERSION = "qpiai-opt-ir/1.0"
_CANONICAL_INDEX_PATTERN = re.compile(r"0|[1-9][0-9]*")


def _fields(
    data: Any,
    context: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{context} must be an object")
    allowed = required | (optional or set())
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(
            f"{context} contains unsupported fields: {', '.join(sorted(map(str, unknown)))}"
        )
    missing = required - data.keys()
    if missing:
        raise ValueError(f"{context} is missing required fields: {', '.join(sorted(missing))}")
    return data


def _number(value: float | int) -> float | int | str:
    if not is_real_or_infinity(value):
        raise ValueError("IR numbers must be real and representable as binary64")
    value = float(value)
    if math.isinf(value):
        return "+inf" if value > 0 else "-inf"
    if not math.isfinite(value):
        raise ValueError("IR numbers cannot be NaN")
    return int(value) if value.is_integer() else value


def _parse_number(value: Any) -> float:
    if value == "+inf":
        return math.inf
    if value == "-inf":
        return -math.inf
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid IR number {value!r}")
    try:
        value = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid IR number {value!r}") from exc
    if not math.isfinite(value):
        raise ValueError("IR numeric values must be finite or use '+inf'/'-inf'")
    return value


def _non_negative_count(value: Any, context: str) -> int:
    if not isinstance(value, numbers.Integral) or isinstance(value, bool) or int(value) < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return int(value)


def _index(value: Any, context: str, *, size: int) -> int:
    if not isinstance(value, numbers.Integral) or isinstance(value, bool):
        raise ValueError(f"{context} must be an integer")
    normalized = int(value)
    if not 0 <= normalized < size:
        raise ValueError(f"{context} {normalized} is outside [0, {size})")
    return normalized


def _name(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError(f"{context} must be a non-empty single-line string")
    return value


def _names(value: Any, context: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    if not allow_empty and not value:
        raise ValueError(f"{context} cannot be empty")
    return [_name(item, f"{context} item {index}") for index, item in enumerate(value)]


def _expression_to_ir(expression: Expression) -> dict[str, Any]:
    return {
        "constant": _number(expression.constant),
        "terms": [
            {
                "coefficient": _number(term.coefficient),
                "variables": [variable.name for variable in term.variables],
            }
            for term in expression.terms
        ],
    }


def _expression_from_ir(data: Mapping[str, Any], variables: Mapping[str, Any]) -> Expression:
    data = _fields(data, "IR expression", required={"constant", "terms"})
    if not isinstance(data["terms"], list):
        raise ValueError("IR expression terms must be an array")
    terms = []
    for index, item in enumerate(data["terms"]):
        item = _fields(
            item,
            f"IR expression term {index}",
            required={"coefficient", "variables"},
        )
        variable_names = _names(
            item["variables"],
            f"IR expression term {index} variables",
            allow_empty=True,
        )
        try:
            factors = [variables[name] for name in variable_names]
        except KeyError as exc:
            raise ValueError(f"IR expression references unknown variable {exc.args[0]!r}") from exc
        terms.append(Term(_parse_number(item["coefficient"]), factors))
    return Expression(terms=terms, constant=_parse_number(data["constant"]))


def _variable_to_ir(variable: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"name": variable.name, "domain": variable.var_type}
    if hasattr(variable, "lower_bound"):
        data["lower_bound"] = _number(variable.lower_bound)
        data["upper_bound"] = _number(variable.upper_bound)
    if isinstance(variable, Fraction):
        data["precision_decimals"] = variable.precision_decimals
    if isinstance(variable, Mixed):
        data["valid_discrete_values"] = [_number(value) for value in variable.valid_discrete_values]
        data["continuous_range"] = [_number(value) for value in variable.continuous_range]
    return data


def _variable_from_ir(data: Mapping[str, Any]):
    data = _fields(
        data,
        "IR variable",
        required={"name", "domain"},
        optional={
            "lower_bound",
            "upper_bound",
            "precision_decimals",
            "valid_discrete_values",
            "continuous_range",
        },
    )
    name = _name(data.get("name"), "IR variable name")
    domain = data.get("domain")
    domain_fields = {
        "BINARY": set(),
        "SPIN": set(),
        "INTEGER": {"lower_bound", "upper_bound"},
        "CONTINUOUS": {"lower_bound", "upper_bound"},
        "SEMI_CONTINUOUS": {"lower_bound", "upper_bound"},
        "SEMI_INTEGER": {"lower_bound", "upper_bound"},
        "FRACTIONAL": {"lower_bound", "upper_bound", "precision_decimals"},
        "MIXED": {"valid_discrete_values", "continuous_range"},
    }
    if not isinstance(domain, str) or domain not in domain_fields:
        raise ValueError(f"Unsupported IR variable domain {domain!r}")
    supplied = set(data) - {"name", "domain"}
    expected = domain_fields[domain]
    if supplied != expected:
        missing = expected - supplied
        extra = supplied - expected
        detail = []
        if missing:
            detail.append("missing " + ", ".join(sorted(missing)))
        if extra:
            detail.append("unsupported " + ", ".join(sorted(extra)))
        raise ValueError(f"IR {domain} variable fields are invalid: {'; '.join(detail)}")
    constructors = {"BINARY": Binary, "SPIN": Spin}
    if domain in constructors:
        return constructors[domain](name)
    if domain == "INTEGER":
        return Integer(name, _parse_number(data["lower_bound"]), _parse_number(data["upper_bound"]))
    if domain == "CONTINUOUS":
        return Continuous(
            name, _parse_number(data["lower_bound"]), _parse_number(data["upper_bound"])
        )
    if domain == "SEMI_CONTINUOUS":
        return SemiContinuous(
            name, _parse_number(data["lower_bound"]), _parse_number(data["upper_bound"])
        )
    if domain == "SEMI_INTEGER":
        return SemiInteger(
            name, _parse_number(data["lower_bound"]), _parse_number(data["upper_bound"])
        )
    if domain == "FRACTIONAL":
        return Fraction(
            name,
            _parse_number(data["lower_bound"]),
            _parse_number(data["upper_bound"]),
            data["precision_decimals"],
        )
    if domain == "MIXED":
        if not isinstance(data["valid_discrete_values"], list):
            raise ValueError("IR MIXED valid_discrete_values must be an array")
        if not isinstance(data["continuous_range"], list) or len(data["continuous_range"]) != 2:
            raise ValueError("IR MIXED continuous_range must contain two items")
        return Mixed(
            name,
            [_parse_number(value) for value in data["valid_discrete_values"]],
            tuple(_parse_number(value) for value in data["continuous_range"]),
        )
    raise AssertionError("validated variable domain was not constructed")  # pragma: no cover


def _relation_to_ir(relation: Equal | LessEqual | GreaterEqual) -> dict[str, Any]:
    kind = {Equal: "equal", LessEqual: "less_equal", GreaterEqual: "greater_equal"}[type(relation)]
    return {
        "kind": kind,
        "lhs": _expression_to_ir(relation.lhs),
        "rhs": _expression_to_ir(relation.rhs),
    }


def _relation_from_ir(data: Mapping[str, Any], variables: Mapping[str, Any]):
    data = _fields(data, "IR relation", required={"kind", "lhs", "rhs"})
    constructors = {"equal": Equal, "less_equal": LessEqual, "greater_equal": GreaterEqual}
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in constructors:
        raise ValueError(f"Unsupported IR relation {kind!r}")
    return constructors[kind](
        _expression_from_ir(data["lhs"], variables),
        _expression_from_ir(data["rhs"], variables),
    )


def _constraint_to_ir(item: Mapping[str, Any]) -> dict[str, Any]:
    constraint = item["constraint"]
    if isinstance(constraint, (Equal, LessEqual, GreaterEqual)):
        data = _relation_to_ir(constraint)
    elif isinstance(constraint, Indicator):
        data = {
            "kind": "indicator",
            "binary": constraint.binary.name,
            "active_value": constraint.active_value,
            "relation": _relation_to_ir(constraint.constraint),
        }
    elif isinstance(constraint, (AND, OR, XOR, NOT)):
        data = {
            "kind": type(constraint).__name__.lower(),
            "variables": [
                variable.name
                for variable in constraint.variables()
                if not getattr(variable, "_is_auxiliary", False)
            ],
        }
    elif isinstance(constraint, CustomPenalty):
        data = {
            "kind": "custom_penalty",
            "expression": _expression_to_ir(constraint.expression),
            "penalty_label": constraint.label,
        }
    elif isinstance(constraint, (SOS1, SOS2)):
        data = {
            "kind": "sos1" if isinstance(constraint, SOS1) else "sos2",
            "name": constraint.name,
            "variables": [variable.name for variable in constraint.variables],
            "weights": [_number(weight) for weight in constraint.weights],
        }
    else:  # pragma: no cover - Model prevents unsupported constraints
        raise TypeError(f"Unsupported constraint {type(constraint).__name__}")
    data["label"] = item.get("label")
    data["penalty_weight"] = None if item.get("weight") is None else _number(item["weight"])
    return data


def _constraint_from_ir(data: Mapping[str, Any], variables: Mapping[str, Any]):
    if not isinstance(data, Mapping):
        raise ValueError("IR constraint must be an object")
    kind = data.get("kind")
    common = {"kind", "label", "penalty_weight"}
    kind_fields = {
        "equal": {"lhs", "rhs"},
        "less_equal": {"lhs", "rhs"},
        "greater_equal": {"lhs", "rhs"},
        "indicator": {"binary", "active_value", "relation"},
        "and": {"variables"},
        "or": {"variables"},
        "xor": {"variables"},
        "not": {"variables"},
        "custom_penalty": {"expression", "penalty_label"},
        "sos1": {"name", "variables", "weights"},
        "sos2": {"name", "variables", "weights"},
    }
    if not isinstance(kind, str) or kind not in kind_fields:
        raise ValueError(f"Unsupported IR constraint kind {kind!r}")
    data = _fields(
        data,
        f"IR {kind} constraint",
        required=common | kind_fields[kind],
    )
    label = data.get("label")
    if label is not None:
        _name(label, f"IR {kind} constraint label")
    if kind == "custom_penalty" and data.get("penalty_label") is not None:
        _name(data["penalty_label"], "IR custom_penalty penalty_label")
    if kind in {"sos1", "sos2"}:
        _name(data["name"], f"IR {kind} name")
    try:
        if kind == "indicator":
            binary_name = _name(data["binary"], "IR indicator binary")
            binary = variables[binary_name]
        elif kind in {"and", "or", "xor", "not", "sos1", "sos2"}:
            expected_arity = 2 if kind == "not" else 3 if kind in {"and", "or", "xor"} else None
            variable_names = _names(data["variables"], f"IR {kind} variables")
            if expected_arity is not None and len(variable_names) != expected_arity:
                raise ValueError(f"IR {kind} variables must contain exactly {expected_arity} names")
            resolved_variables = [variables[name] for name in variable_names]
    except KeyError as exc:
        raise ValueError(f"IR constraint references unknown variable {exc.args[0]!r}") from exc
    if kind in {"equal", "less_equal", "greater_equal"}:
        relation_data = {key: data[key] for key in ("kind", "lhs", "rhs")}
        constraint = _relation_from_ir(relation_data, variables)
    elif kind == "indicator":
        constraint = Indicator(
            binary,
            data["active_value"],
            _relation_from_ir(data["relation"], variables),
        )
    elif kind in {"and", "or", "xor", "not"}:
        constructors = {"and": AND, "or": OR, "xor": XOR, "not": NOT}
        constraint = constructors[kind](*resolved_variables)
    elif kind == "custom_penalty":
        constraint = CustomPenalty(
            _expression_from_ir(data["expression"], variables),
            label=data.get("penalty_label"),
        )
    elif kind in {"sos1", "sos2"}:
        if not isinstance(data["weights"], list):
            raise ValueError(f"IR {kind} weights must be an array")
        constructor = SOS1 if kind == "sos1" else SOS2
        constraint = constructor(
            data["name"],
            resolved_variables,
            [_parse_number(value) for value in data["weights"]],
        )
    weight = data.get("penalty_weight")
    return constraint, None if weight is None else _parse_number(weight), data.get("label")


def _model_to_ir(model: Model) -> dict[str, Any]:
    model._require_model()
    return {
        "ir_version": IR_VERSION,
        "kind": "model",
        "name": model.name,
        "sense": model.sense,
        "variables": [
            _variable_to_ir(variable)
            for variable in model.variables()
            if not getattr(variable, "_is_auxiliary", False)
            or getattr(variable, "_persist_in_ir", False)
        ],
        "objective": _expression_to_ir(model.objective),
        "constraints": [_constraint_to_ir(item) for item in model.constraints],
        "parameters": [
            parameter.as_dict() for parameter in getattr(model, "parameters", {}).values()
        ],
        "parameter_bindings": deepcopy(getattr(model, "_parameter_bindings", [])),
        "transformation_metadata": deepcopy(getattr(model, "transformation_metadata", [])),
    }


def _model_from_ir(data: Mapping[str, Any]) -> Model:
    data = _fields(
        data,
        "model IR",
        required={
            "ir_version",
            "kind",
            "name",
            "sense",
            "variables",
            "objective",
            "constraints",
            "parameters",
            "parameter_bindings",
            "transformation_metadata",
        },
    )
    if not isinstance(data["variables"], list) or not isinstance(data["constraints"], list):
        raise ValueError("model IR variables and constraints must be arrays")
    model = Model(data.get("name", "Model"))
    variables = {}
    for variable_data in data.get("variables", []):
        variable = _variable_from_ir(variable_data)
        if variable.name in variables:
            raise ValueError(f"Duplicate IR variable name {variable.name!r}")
        variables[variable.name] = variable
        model.add_variable(variable)
    objective = _expression_from_ir(data["objective"], variables)
    if data.get("sense") == "MIN":
        model.minimize(objective)
    elif data.get("sense") == "MAX":
        model.maximize(objective)
    else:
        raise ValueError("IR model sense must be MIN or MAX")
    for constraint_data in data.get("constraints", []):
        constraint, weight, label = _constraint_from_ir(constraint_data, variables)
        model.add_constraint(constraint, penalty_weight=weight, label=label)
    metadata = data.get("transformation_metadata", [])
    parameters = data.get("parameters", [])
    bindings = data.get("parameter_bindings", [])
    if not isinstance(metadata, list):
        raise ValueError("model IR transformation_metadata must be an array")
    if not isinstance(parameters, list) or not isinstance(bindings, list):
        raise ValueError("model IR parameters and parameter_bindings must be arrays")
    validated_metadata = []
    for index, record in enumerate(metadata):
        if not isinstance(record, Mapping):
            raise ValueError(f"IR transformation_metadata item {index} must be an object")
        record = dict(record)
        if record.get("transformation") == "piecewise_linear_sos2":
            record = dict(
                _fields(
                    record,
                    f"IR piecewise transformation {index}",
                    required={
                        "stage",
                        "transformation",
                        "name",
                        "source_variable",
                        "output_variable",
                        "breakpoints",
                        "values",
                        "weight_variables",
                    },
                )
            )
            if record["stage"] != "modelling":
                raise ValueError(f"IR piecewise transformation {index} stage must be 'modelling'")
            _name(record["name"], f"IR piecewise transformation {index} name")
            source_name = _name(
                record["source_variable"],
                f"IR piecewise transformation {index} source_variable",
            )
            output_name = _name(
                record["output_variable"],
                f"IR piecewise transformation {index} output_variable",
            )
            if source_name == output_name:
                raise ValueError(
                    f"IR piecewise transformation {index} source and output must be distinct"
                )
            weight_names = _names(
                record["weight_variables"],
                f"IR piecewise transformation {index} weight_variables",
            )
            if len(set(weight_names)) != len(weight_names):
                raise ValueError(
                    f"IR piecewise transformation {index} weight_variables must be unique"
                )
            if source_name in weight_names or output_name in weight_names:
                raise ValueError(
                    f"IR piecewise transformation {index} roles must use distinct variables"
                )
            if not isinstance(record["breakpoints"], list) or not isinstance(
                record["values"], list
            ):
                raise ValueError(
                    f"IR piecewise transformation {index} breakpoints and values must be arrays"
                )
            breakpoints = tuple(_parse_number(value) for value in record["breakpoints"])
            point_values = tuple(_parse_number(value) for value in record["values"])
            if (
                len(breakpoints) < 2
                or len(breakpoints) != len(point_values)
                or len(breakpoints) != len(weight_names)
            ):
                raise ValueError(
                    f"IR piecewise transformation {index} coordinate and weight counts must "
                    "match and contain at least two points"
                )
            if any(not math.isfinite(value) for value in breakpoints + point_values):
                raise ValueError(f"IR piecewise transformation {index} coordinates must be finite")
            if any(right <= left for left, right in zip(breakpoints, breakpoints[1:])):
                raise ValueError(
                    f"IR piecewise transformation {index} breakpoints must be strictly increasing"
                )
            missing_names = [
                name for name in (source_name, output_name, *weight_names) if name not in variables
            ]
            if missing_names:
                raise ValueError(
                    f"IR piecewise transformation {index} references unknown variables: "
                    + ", ".join(map(repr, missing_names))
                )
            weight_variables = tuple(variables[name] for name in weight_names)
            if any(
                not isinstance(variable, Continuous)
                or variable.lower_bound != 0.0
                or variable.upper_bound != 1.0
                for variable in weight_variables
            ):
                raise ValueError(
                    f"IR piecewise transformation {index} weights must be continuous in [0, 1]"
                )

            expected_relations = (
                quicksum(weight_variables) - 1.0,
                variables[source_name]
                - quicksum(
                    breakpoint * variable
                    for breakpoint, variable in zip(breakpoints, weight_variables)
                ),
                variables[output_name]
                - quicksum(
                    value * variable for value, variable in zip(point_values, weight_variables)
                ),
            )

            def signature(expression):
                return (
                    expression.constant,
                    tuple(
                        sorted(
                            (
                                tuple(variable.name for variable in term.variables),
                                term.coefficient,
                            )
                            for term in expression.terms
                        )
                    ),
                )

            relation_signatures = {
                signature(item["constraint"].lhs - item["constraint"].rhs)
                for item in model.constraints
                if isinstance(item["constraint"], Equal)
            }
            if any(
                signature(expression) not in relation_signatures
                for expression in expected_relations
            ):
                raise ValueError(
                    f"IR piecewise transformation {index} does not match its linking constraints"
                )
            if not any(
                isinstance(item["constraint"], SOS2)
                and len(item["constraint"].variables) == len(weight_variables)
                and all(
                    actual is expected
                    for actual, expected in zip(
                        item["constraint"].variables,
                        weight_variables,
                    )
                )
                and tuple(item["constraint"].weights) == breakpoints
                for item in model.constraints
            ):
                raise ValueError(
                    f"IR piecewise transformation {index} does not match an SOS2 constraint"
                )
            for variable in weight_variables:
                variable._is_auxiliary = True
                variable._persist_in_ir = True
        validated_metadata.append(deepcopy(record))
    model.transformation_metadata = validated_metadata
    if parameters:
        from .parameters import Parameter

        for index, parameter_data in enumerate(parameters):
            parameter_data = _fields(
                parameter_data,
                f"IR parameter {index}",
                required={"name", "value"},
            )
            model.add_parameter(Parameter(parameter_data["name"], parameter_data["value"]))
    for index, binding in enumerate(bindings):
        if not isinstance(binding, Mapping):
            raise ValueError(f"IR parameter binding {index} must be an object")
        target = binding.get("target")
        target_field = "constraint" if target == "constraint_rhs" else "variable"
        binding = _fields(
            binding,
            f"IR parameter binding {index}",
            required={"parameter", "target", target_field},
        )
        validated_binding = model._parameter_binding(**binding)
        if validated_binding in model._parameter_bindings:
            raise ValueError(f"IR parameter binding {index} is duplicated")
        model._parameter_bindings.append(validated_binding)
    return model


def to_ir(value: Any) -> dict[str, Any]:
    """Serialize a model or native polynomial representation to canonical IR."""

    from ..opt_solvers.hubo.models import HUBOProblem
    from ..opt_solvers.ising import IsingProblem
    from ..opt_solvers.milp import MILPProblem
    from ..opt_solvers.qubo.models import QUBOProblem

    if isinstance(value, MILPProblem):
        return _model_to_ir(value._as_model())
    if isinstance(value, Model):
        return _model_to_ir(value)
    if isinstance(value, QUBOProblem):
        return {
            "ir_version": IR_VERSION,
            "kind": "qubo",
            "num_variables": value.num_variables,
            "terms": [
                {"indices": list(key), "coefficient": _number(coefficient)}
                for key, coefficient in sorted(value.qubo_dict.items())
            ],
            "matrix_format": value.matrix_format,
            "offset": _number(value.offset),
            "sense": value.sense,
            "variable_names": list(value.variable_names or ()),
        }
    if isinstance(value, HUBOProblem):
        return {
            "ir_version": IR_VERSION,
            "kind": "hubo",
            "num_variables": value.num_variables,
            "terms": [
                {"variables": list(term), "coefficient": _number(coefficient)}
                for term, coefficient in sorted(
                    value.polynomial_dict.items(), key=lambda item: repr(item[0])
                )
            ],
            "sense": value.sense,
            "variable_names": list(value.variable_names or ()),
        }
    if isinstance(value, IsingProblem):
        return {
            "ir_version": IR_VERSION,
            "kind": "ising",
            "num_variables": value.num_variables,
            "linear": {str(key): _number(coefficient) for key, coefficient in value.linear.items()},
            "quadratic": [
                {"indices": list(key), "coefficient": _number(coefficient)}
                for key, coefficient in sorted(value.quadratic.items())
            ],
            "offset": _number(value.offset),
            "sense": value.sense,
            "variable_names": list(value.variable_names),
        }
    from ..quantum._shared import _is_qubit_hamiltonian, _n_qubits, _terms

    if _is_qubit_hamiltonian(value):
        terms = _terms(value)
        count = _n_qubits(terms, value)
        names = tuple(getattr(value, "variable_names", ()))
        if names:
            from .converters import _validated_variable_names

            names = _validated_variable_names(names, count)
        sense = getattr(value, "original_sense", "MIN")
        if sense not in {"MIN", "MAX"}:
            raise ValueError("Hamiltonian original_sense must be MIN or MAX")
        return {
            "ir_version": IR_VERSION,
            "kind": "qubit_hamiltonian",
            "num_qubits": count,
            "terms": [
                {
                    "operators": [[index, pauli] for index, pauli in operators],
                    "coefficient": _number(coefficient),
                }
                for operators, coefficient in terms
            ],
            "sense": sense,
            "variable_names": list(names),
        }
    raise TypeError("to_ir expects Model, a native problem, or QubitHamiltonian")


def from_ir(document: Mapping[str, Any]) -> Any:
    """Validate and deserialize a canonical IR document."""

    if not isinstance(document, Mapping):
        raise TypeError("IR document must be a mapping")
    data = dict(document)
    if data.get("ir_version") != IR_VERSION:
        raise ValueError(
            f"Unsupported IR version {data.get('ir_version')!r}; expected {IR_VERSION}"
        )
    kind = data.get("kind")
    if kind == "model":
        return _model_from_ir(data)
    if kind == "qubo":
        from ..opt_solvers.qubo.models import QUBOProblem

        data = _fields(
            data,
            "QUBO IR",
            required={
                "ir_version",
                "kind",
                "num_variables",
                "terms",
                "matrix_format",
                "offset",
                "sense",
                "variable_names",
            },
        )
        if not isinstance(data["terms"], list):
            raise ValueError("QUBO IR terms must be an array")
        num_variables = _non_negative_count(data["num_variables"], "QUBO IR num_variables")
        variable_names = _names(data["variable_names"], "QUBO IR variable_names", allow_empty=True)
        terms = {}
        for index, item in enumerate(data["terms"]):
            item = _fields(
                item,
                f"QUBO IR term {index}",
                required={"indices", "coefficient"},
            )
            if not isinstance(item["indices"], list) or len(item["indices"]) != 2:
                raise ValueError(f"QUBO IR term {index} indices must contain two items")
            key = tuple(
                _index(value, f"QUBO IR term {index} index {position}", size=num_variables)
                for position, value in enumerate(item["indices"])
            )
            if key in terms:
                raise ValueError(f"QUBO IR contains duplicate term {key!r}")
            terms[key] = _parse_number(item["coefficient"])
        return QUBOProblem(
            num_variables=num_variables,
            qubo_dict=terms,
            matrix_format=data["matrix_format"],
            offset=_parse_number(data.get("offset", 0)),
            sense=data.get("sense", "MIN"),
            variable_names=tuple(variable_names) or None,
        )
    if kind == "hubo":
        from ..opt_solvers.hubo.models import HUBOProblem

        data = _fields(
            data,
            "HUBO IR",
            required={"ir_version", "kind", "num_variables", "terms", "sense", "variable_names"},
        )
        if not isinstance(data["terms"], list):
            raise ValueError("HUBO IR terms must be an array")
        num_variables = _non_negative_count(data["num_variables"], "HUBO IR num_variables")
        terms = {}
        for index, item in enumerate(data["terms"]):
            item = _fields(
                item,
                f"HUBO IR term {index}",
                required={"variables", "coefficient"},
            )
            labels = _names(item["variables"], f"HUBO IR term {index} variables", allow_empty=True)
            if len(labels) != len(set(labels)):
                raise ValueError(f"HUBO IR term {index} repeats a variable")
            canonical_key = tuple(sorted(labels))
            if canonical_key in terms:
                raise ValueError(f"HUBO IR contains duplicate term {canonical_key!r}")
            terms[canonical_key] = _parse_number(item["coefficient"])
        return HUBOProblem(
            num_variables=num_variables,
            polynomial_dict=terms,
            sense=data.get("sense", "MIN"),
            variable_names=tuple(
                _names(data["variable_names"], "HUBO IR variable_names", allow_empty=True)
            )
            or None,
        )
    if kind == "ising":
        data = _fields(
            data,
            "Ising IR",
            required={
                "ir_version",
                "kind",
                "num_variables",
                "linear",
                "quadratic",
                "offset",
                "sense",
                "variable_names",
            },
        )
        if not isinstance(data["linear"], Mapping) or not isinstance(data["quadratic"], list):
            raise ValueError("Ising IR linear must be an object and quadratic must be an array")
        num_variables = _non_negative_count(data["num_variables"], "Ising IR num_variables")
        variable_names = _names(
            data["variable_names"],
            "Ising IR variable_names",
            allow_empty=True,
        )
        linear = {}
        for raw_key, value in data["linear"].items():
            if not isinstance(raw_key, str) or _CANONICAL_INDEX_PATTERN.fullmatch(raw_key) is None:
                raise ValueError(
                    f"Ising IR linear index {raw_key!r} must use canonical non-negative "
                    "integer text"
                )
            key = _index(int(raw_key), f"Ising IR linear index {raw_key!r}", size=num_variables)
            if key in linear:
                raise ValueError(f"Ising IR contains duplicate linear index {key}")
            linear[key] = _parse_number(value)
        quadratic = {}
        for index, item in enumerate(data["quadratic"]):
            item = _fields(
                item,
                f"Ising IR quadratic term {index}",
                required={"indices", "coefficient"},
            )
            if not isinstance(item["indices"], list) or len(item["indices"]) != 2:
                raise ValueError(f"Ising IR quadratic term {index} indices must contain two items")
            key = tuple(
                sorted(
                    _index(
                        value,
                        f"Ising IR quadratic term {index} index {position}",
                        size=num_variables,
                    )
                    for position, value in enumerate(item["indices"])
                )
            )
            if key in quadratic:
                raise ValueError(f"Ising IR contains duplicate quadratic term {key!r}")
            quadratic[key] = _parse_number(item["coefficient"])
        from ..opt_solvers.ising import IsingProblem

        return IsingProblem(
            linear=linear,
            quadratic=quadratic,
            offset=_parse_number(data.get("offset", 0)),
            num_variables=num_variables,
            variable_names=tuple(variable_names),
            sense=data.get("sense", "MIN"),
        )
    if kind == "qubit_hamiltonian":
        data = _fields(
            data,
            "QubitHamiltonian IR",
            required={
                "ir_version",
                "kind",
                "num_qubits",
                "terms",
                "sense",
                "variable_names",
            },
        )
        count = _non_negative_count(data["num_qubits"], "QubitHamiltonian IR num_qubits")
        names = _names(
            data["variable_names"], "QubitHamiltonian IR variable_names", allow_empty=True
        )
        if names and (len(names) != count or len(set(names)) != count):
            raise ValueError("Hamiltonian variable_names must contain one unique name per qubit")
        if data["sense"] not in ("MIN", "MAX"):
            raise ValueError("Hamiltonian IR sense must be MIN or MAX")
        if not isinstance(data["terms"], list):
            raise ValueError("Hamiltonian IR terms must be an array")
        terms = []
        for position, item in enumerate(data["terms"]):
            item = _fields(
                item, f"Hamiltonian IR term {position}", required={"operators", "coefficient"}
            )
            if not isinstance(item["operators"], list):
                raise ValueError("Hamiltonian IR operators must be an array")
            operators = []
            for operator in item["operators"]:
                if not isinstance(operator, list) or len(operator) != 2:
                    raise ValueError("Hamiltonian IR operators must be [qubit, Pauli] pairs")
                index = _index(operator[0], "Hamiltonian IR qubit", size=count)
                pauli = operator[1]
                if not isinstance(pauli, str) or pauli not in ("I", "X", "Y", "Z"):
                    raise ValueError("Hamiltonian IR Pauli must be I, X, Y, or Z")
                operators.append((index, pauli))
            terms.append((operators, _parse_number(item["coefficient"])))
        from ..quantum._shared import _quantum_types, _terms

        _, hamiltonian_type, _, _ = _quantum_types()
        hamiltonian = hamiltonian_type(terms)
        hamiltonian.n_qubits = count
        hamiltonian.variable_names = tuple(names)
        hamiltonian.original_sense = data["sense"]
        _terms(hamiltonian)
        return hamiltonian
    raise ValueError(f"Unsupported IR kind {kind!r}")


def dumps(value: Any, *, indent: int | None = None) -> str:
    """Serialize to strict JSON; NaN and implicit infinities are rejected."""

    return json.dumps(to_ir(value), indent=indent, sort_keys=True, allow_nan=False)


def loads(payload: str | bytes | bytearray) -> Any:
    """Parse strict canonical IR JSON and return its model or problem."""

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"IR JSON contains duplicate object key {key!r}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"IR JSON contains non-standard numeric token {value}")

    return from_ir(
        json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    )


def dump(value: Any, path: str | Path, *, indent: int = 2) -> Path:
    """Write canonical IR JSON and return the destination path."""

    target = Path(path)
    target.write_text(dumps(value, indent=indent) + "\n", encoding="utf-8")
    return target


def load(path: str | Path) -> Any:
    """Read canonical IR JSON from a path."""

    return loads(Path(path).read_text(encoding="utf-8"))
