"""LP-format export for validated linear model matrices."""

import math

from ..._numeric import is_finite_real, is_real_or_infinity
from ..constraints import SOS1, SOS2
from ..expressions import quicksum
from ..model import Model
from ..variables import Binary, Continuous, Integer, SemiContinuous, SemiInteger

_TYPE_SECTIONS = ("general", "binary", "semi_continuous", "semi_integer")
_MODEL_FIELDS = {
    "name",
    "objective",
    "constraints",
    "sos",
    "bounds",
    *_TYPE_SECTIONS,
}
_OBJECTIVE_FIELDS = {"sense", "terms", "constant"}
_CONSTRAINT_FIELDS = {"name", "sense", "terms", "rhs"}
_SOS_FIELDS = {"name", "type", "vars"}


def generate_lp(model_dict):
    """Build and export a linear model from the SDK dictionary interchange form.

    The function is intentionally strict: malformed coefficients, undeclared
    senses, overlapping type declarations, and invalid SOS data fail before a
    partially valid model can be exported.
    """

    normalized = _validate_model_dict(model_dict)
    model = Model(normalized["name"])
    variables = _build_variables(normalized)
    model.add_variables(variables.values())

    objective = normalized["objective"]
    objective_expression = _build_expression(objective["terms"], variables) + objective["constant"]
    if objective["sense"] == "max":
        model.maximize(objective_expression)
    else:
        model.minimize(objective_expression)

    for index, constraint in enumerate(normalized["constraints"]):
        expression = _build_expression(constraint["terms"], variables)
        if constraint["sense"] == "<=":
            relation = expression <= constraint["rhs"]
        elif constraint["sense"] == ">=":
            relation = expression >= constraint["rhs"]
        else:
            relation = expression == constraint["rhs"]
        model.add_constraint(relation, label=constraint["name"] or f"c{index}")

    existing_constraint_names = {
        model._constraint_name(index, item) for index, item in enumerate(model.constraints)
    }
    for name in sorted(normalized["binary"]):
        lower, upper = normalized["bounds"].get(name, (0.0, 1.0))
        if lower == upper:
            base = f"__qpiai_binary_fixed_{name}"
            label = model._available_generated_name(base, existing_constraint_names)
            existing_constraint_names.add(label)
            model.add_constraint(variables[name] == lower, label=label)

    for sos in normalized["sos"]:
        members = [variables[name] for name in sos["variables"]]
        constraint_type = SOS1 if sos["type"] == "S1" else SOS2
        model.add_constraint(constraint_type(sos["name"], members, weights=sos["weights"]))

    return model.to_lp_string(), model.to_lp_matrix()


def _finite_number(value, description):
    if not is_finite_real(value):
        raise ValueError(f"{description} must be a finite real number")
    return float(value)


def _normalize_name_list(model_dict, section):
    raw = model_dict.get(section, [])
    if not isinstance(raw, (list, tuple, set)):
        raise ValueError(f"{section} must be a list, tuple, or set of variable names")
    result = set()
    for name in raw:
        if not isinstance(name, str) or not name:
            raise ValueError(f"{section} contains an invalid variable name")
        result.add(name)
    return result


def _normalize_terms(raw, description):
    if not isinstance(raw, dict):
        raise ValueError(f"{description} terms must be a dictionary")
    normalized = {}
    for name, coefficient in raw.items():
        _validate_variable_name(name, description)
        value = _finite_number(coefficient, f"Coefficient of '{name}' in {description}")
        if value != 0.0:
            normalized[name] = value
    return normalized


def _validate_variable_name(name, description):
    if not isinstance(name, str) or not name:
        raise ValueError(f"{description} contains an invalid variable name")
    return True


def _normalize_bound(value, *, default, description):
    if value is None:
        return default
    if not is_real_or_infinity(value):
        raise ValueError(f"{description} must be numeric, None, or infinity")
    value = float(value)
    return value


def _reject_unknown_fields(value, allowed, description):
    unknown = set(value) - set(allowed)
    if unknown:
        fields = ", ".join(sorted(map(str, unknown)))
        raise ValueError(f"{description} contains unsupported fields: {fields}")


def _validate_model_dict(model_dict):
    if not isinstance(model_dict, dict):
        raise ValueError("Model input must be a dictionary")
    _reject_unknown_fields(model_dict, _MODEL_FIELDS, "Model")
    name = model_dict.get("name", "GeneratedLP")
    if not isinstance(name, str) or not name:
        raise ValueError("Model name must be a non-empty string")

    raw_objective = model_dict.get("objective")
    if not isinstance(raw_objective, dict):
        raise ValueError("Model objective must be a dictionary")
    _reject_unknown_fields(raw_objective, _OBJECTIVE_FIELDS, "Model objective")
    raw_sense = raw_objective.get("sense", "min")
    if not isinstance(raw_sense, str):
        raise ValueError("Objective sense must be 'min' or 'max'")
    sense = raw_sense.lower()
    if sense not in {"min", "max"}:
        raise ValueError("Objective sense must be 'min' or 'max'")
    objective = {
        "sense": sense,
        "terms": _normalize_terms(raw_objective.get("terms", {}), "objective"),
        "constant": _finite_number(raw_objective.get("constant", 0.0), "Objective constant"),
    }

    sections = {section: _normalize_name_list(model_dict, section) for section in _TYPE_SECTIONS}
    memberships = {}
    for section, names in sections.items():
        for variable_name in names:
            previous = memberships.setdefault(variable_name, section)
            if previous != section:
                raise ValueError(
                    f"Variable '{variable_name}' is declared in both {previous} and {section}"
                )

    raw_bounds = model_dict.get("bounds", {})
    if not isinstance(raw_bounds, dict):
        raise ValueError("bounds must be a dictionary")
    bounds = {}
    for variable_name, bound in raw_bounds.items():
        _validate_variable_name(variable_name, "bounds")
        if not isinstance(bound, (tuple, list)) or len(bound) != 2:
            raise ValueError(f"Bounds for '{variable_name}' must be a (lower, upper) pair")
        lower = _normalize_bound(
            bound[0], default=-math.inf, description=f"Lower bound for '{variable_name}'"
        )
        upper = _normalize_bound(
            bound[1], default=math.inf, description=f"Upper bound for '{variable_name}'"
        )
        if lower > upper:
            raise ValueError(f"Bounds for '{variable_name}' have lower > upper")
        bounds[variable_name] = (lower, upper)

    constraints = []
    raw_constraints = model_dict.get("constraints", [])
    if not isinstance(raw_constraints, (list, tuple)):
        raise ValueError("constraints must be a list or tuple")
    for index, raw in enumerate(raw_constraints):
        if not isinstance(raw, dict):
            raise ValueError(f"Constraint {index} must be a dictionary")
        _reject_unknown_fields(raw, _CONSTRAINT_FIELDS, f"Constraint {index}")
        constraint_sense = raw.get("sense")
        if constraint_sense not in {"<=", ">=", "="}:
            raise ValueError(f"Constraint {index} has invalid sense {constraint_sense!r}")
        constraint_name = raw.get("name")
        if constraint_name is not None and (
            not isinstance(constraint_name, str) or not constraint_name
        ):
            raise ValueError(f"Constraint {index} has an invalid name")
        constraints.append(
            {
                "name": constraint_name,
                "sense": constraint_sense,
                "terms": _normalize_terms(raw.get("terms", {}), f"constraint {index}"),
                "rhs": _finite_number(raw.get("rhs"), f"RHS of constraint {index}"),
            }
        )

    sos_sets = []
    raw_sos = model_dict.get("sos", [])
    if not isinstance(raw_sos, (list, tuple)):
        raise ValueError("sos must be a list or tuple")
    for index, raw in enumerate(raw_sos):
        if not isinstance(raw, dict):
            raise ValueError(f"SOS set {index} must be a dictionary")
        _reject_unknown_fields(raw, _SOS_FIELDS, f"SOS set {index}")
        sos_type = raw.get("type")
        if sos_type not in {"S1", "S2"}:
            raise ValueError(f"SOS set {index} has invalid type {sos_type!r}")
        sos_name = raw.get("name")
        if not isinstance(sos_name, str) or not sos_name:
            raise ValueError(f"SOS set {index} requires a non-empty name")
        raw_members = raw.get("vars")
        if not isinstance(raw_members, dict) or not raw_members:
            raise ValueError(f"SOS set '{sos_name}' vars must be a non-empty dictionary")
        variables = list(raw_members)
        for variable_name in variables:
            _validate_variable_name(variable_name, f"SOS set '{sos_name}'")
        weights = [
            _finite_number(weight, f"Weight of '{variable_name}' in SOS set '{sos_name}'")
            for variable_name, weight in raw_members.items()
        ]
        if len(weights) != len(set(weights)):
            raise ValueError(f"SOS set '{sos_name}' contains duplicate weights")
        sos_sets.append(
            {
                "name": sos_name,
                "type": sos_type,
                "variables": variables,
                "weights": weights,
            }
        )

    variable_names = set(objective["terms"]) | set(bounds) | set(memberships)
    for constraint in constraints:
        variable_names.update(constraint["terms"])
    for sos in sos_sets:
        variable_names.update(sos["variables"])
    if not variable_names:
        raise ValueError("Model must contain at least one decision variable")

    for variable_name in sections["binary"]:
        lower, upper = bounds.get(variable_name, (0.0, 1.0))
        if lower not in {0.0, 1.0} or upper not in {0.0, 1.0}:
            raise ValueError(
                f"Binary bounds for '{variable_name}' must be 0 or 1, not [{lower}, {upper}]"
            )
    for section in ("semi_continuous", "semi_integer"):
        for variable_name in sections[section]:
            if variable_name not in bounds:
                raise ValueError(f"{section} variable '{variable_name}' requires finite bounds")
            lower, upper = bounds[variable_name]
            if not math.isfinite(lower) or not math.isfinite(upper) or lower <= 0.0:
                raise ValueError(
                    f"{section} variable '{variable_name}' requires 0 < lower <= upper"
                )

    return {
        "name": name,
        "objective": objective,
        "constraints": constraints,
        "sos": sos_sets,
        "bounds": bounds,
        "variable_names": variable_names,
        **sections,
    }


def _build_variables(model_dict):
    variables = {}
    bounds = model_dict["bounds"]
    for name in sorted(model_dict["variable_names"]):
        if name in model_dict["binary"]:
            variables[name] = Binary(name)
            continue
        lower, upper = bounds.get(name, (0.0, math.inf))
        if name in model_dict["general"]:
            variables[name] = Integer(name, lower, upper)
        elif name in model_dict["semi_continuous"]:
            variables[name] = SemiContinuous(name, lower, upper)
        elif name in model_dict["semi_integer"]:
            variables[name] = SemiInteger(name, lower, upper)
        else:
            variables[name] = Continuous(name, lower, upper)
    return variables


def _build_expression(term_dict, variables):
    return quicksum(
        coefficient * variables[variable_name] for variable_name, coefficient in term_dict.items()
    )
