"""Optimization model construction, validation, transformation, and export."""

import gzip
import math
import numbers
import re
import textwrap
import warnings
from bisect import bisect_right
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import ArpackNoConvergence, eigsh

from .._numeric import (
    DEFAULT_FEASIBILITY_TOLERANCE,
    finite_fsum,
    fsum_by_key,
    is_finite_real,
)
from .constraints import (
    SOS1,
    SOS2,
    CustomPenalty,
    Equal,
    GreaterEqual,
    Indicator,
    LessEqual,
    LogicalConstraint,
)
from .constraints.relational import (
    expression_bounds,
    expression_lattice_step,
    linear_coefficients,
)
from .encoders.dispatcher import _encode_variables, dispatch_encoders
from .expressions import Expression, Term, _finite_product, quicksum
from .utils.exceptions import (
    ConstraintValidationError,
    EmptyModelError,
    EncodingError,
    InvalidExpressionError,
    InvalidLinearModelError,
    InvalidQUBOError,
    ModelCompilationError,
    ModelValidationError,
    NumericalStabilityError,
    ObjectiveError,
)
from .variables import Variable, _fixed_point_units

_LP_NAME_PATTERN = re.compile(r"^[A-Za-z_.$][A-Za-z0-9_.$\[\],]*$")
_LP_BRACKET_HEADER = "\\ qpiai-opt-lp-names/1: [] -> ()"
_LP_ENCODE_NAMES = str.maketrans("[]", "()")
_LP_DECODE_NAMES = str.maketrans("()", "[]")
_LP_RESERVED_NAMES = {
    "binary",
    "binaries",
    "bin",
    "bounds",
    "end",
    "free",
    "general",
    "generals",
    "gen",
    "inf",
    "infinity",
    "max",
    "maximize",
    "maximum",
    "min",
    "minimize",
    "minimum",
    "semi",
    "semis",
    "semi-continuous",
    "sos",
    "st",
}
_CONSTANT_EXPORT_ANCHOR = "__qpiai_constant_anchor"
_DENSE_QP_CURVATURE_LIMIT = 256


def _validate_boolean_option(value, name):
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _qp_curvature(hessian, sense):
    """Return the extreme signed eigenvalue and its numerical tolerance."""

    if hessian.shape[0] == 0 or hessian.nnz == 0:
        return 0.0, 1e-9
    spectral_scale = max(1.0, float(sparse.linalg.norm(hessian, ord=np.inf)))
    tolerance = 1e-9 * spectral_scale
    active_indices = np.unique(hessian.nonzero()[0])
    has_zero_eigenvalue = len(active_indices) < hessian.shape[0]
    active_hessian = hessian[active_indices][:, active_indices]
    signed_hessian = active_hessian if sense == "MIN" else -active_hessian
    count = signed_hessian.shape[0]
    if count <= _DENSE_QP_CURVATURE_LIMIT:
        extreme = float(np.linalg.eigvalsh(signed_hessian.toarray())[0])
        return min(0.0, extreme) if has_zero_eigenvalue else extreme, tolerance
    try:
        extreme = float(
            eigsh(
                signed_hessian,
                k=1,
                which="SA",
                return_eigenvectors=False,
                tol=0.0,
                maxiter=max(1_000, 10 * count),
            )[0]
        )
    except ArpackNoConvergence as exc:
        raise NumericalStabilityError(
            "Continuous QP curvature could not be verified reliably; reduce or rescale the model"
        ) from exc
    if -tolerance <= extreme < 0.0:
        diagonal = signed_hessian.diagonal()
        off_diagonal_sum = np.asarray(abs(signed_hessian).sum(axis=1)).ravel() - abs(diagonal)
        if np.all(diagonal >= off_diagonal_sum):
            extreme = 0.0
    return min(0.0, extreme) if has_zero_eigenvalue else extreme, tolerance


class Model:
    """A model container for LP/MILP, continuous QP, QUBO, HUBO, and Ising workflows."""

    def __init__(self, name="Model"):
        if (
            not isinstance(name, str)
            or not name
            or any(character in name for character in "\x00\r\n")
        ):
            raise ModelValidationError("Model name must be a non-empty single-line string")
        self.name = name
        self.objective = None
        self.constraints = []
        self.sense = "MIN"
        self.qubo_variable_names = []
        self.qubo_variable_indices = {}
        self.hubo_variable_names = []
        self.encoding_map = {}
        self._declared_variables = []
        self.parameters = {}
        self._parameter_bindings = []
        self.transformation_metadata = []

    def minimize(self, expression):
        """Set and return a minimization objective."""

        self.objective = self._coerce_objective_expression(expression, "minimize")
        self.sense = "MIN"
        return self.objective

    def maximize(self, expression):
        """Set and return a maximization objective."""

        self.objective = self._coerce_objective_expression(expression, "maximize")
        self.sense = "MAX"
        return self.objective

    def add_variable(self, variable):
        """Register a decision variable even when its current coefficient is zero."""

        return self.add_variables([variable])[0]

    def add_variables(self, variables):
        """Register an iterable of decision variables and return them as a list."""

        try:
            candidates = list(variables)
        except TypeError as exc:
            raise ModelValidationError("add_variables expects an iterable of Variables") from exc

        known_by_name = {variable.name: variable for variable in self.variables()}
        new_variables = []
        new_identities = set()
        declared_identities = {id(variable) for variable in self._declared_variables}
        for index, variable in enumerate(candidates):
            if not isinstance(variable, Variable):
                raise ModelValidationError(f"add_variables item {index} is not a decision Variable")
            existing = known_by_name.get(variable.name)
            if existing is not None and existing is not variable:
                raise ModelValidationError(
                    f"Model '{self.name}' already contains a distinct variable named "
                    f"'{variable.name}'"
                )
            known_by_name[variable.name] = variable
            identity = id(variable)
            if identity not in declared_identities and identity not in new_identities:
                new_variables.append(variable)
                new_identities.add(identity)

        self._declared_variables.extend(new_variables)
        return candidates

    def add_indexed_variables(self, indexed_variables):
        """Register every member of an IndexedVariables container."""

        from .indexed import IndexedVariables

        if not isinstance(indexed_variables, IndexedVariables):
            raise ModelValidationError("add_indexed_variables expects IndexedVariables")
        self.add_variables(indexed_variables.values())
        return indexed_variables

    def _resolve_variable(self, variable_or_name):
        if isinstance(variable_or_name, Variable):
            if not any(item is variable_or_name for item in self.variables()):
                raise ModelValidationError(
                    f"Variable '{variable_or_name.name}' does not belong to model '{self.name}'"
                )
            return variable_or_name
        if isinstance(variable_or_name, str):
            matches = [
                variable for variable in self.variables() if variable.name == variable_or_name
            ]
            if len(matches) == 1:
                return matches[0]
        raise ModelValidationError(f"Unknown model variable {variable_or_name!r}")

    def set_objective_coefficient(self, variable_or_name, coefficient):
        """Replace one variable's linear objective coefficient in place."""

        from .parameters import Parameter

        variable = self._resolve_variable(variable_or_name)
        if isinstance(coefficient, Parameter):
            self.bind_parameter(
                coefficient,
                target="objective_coefficient",
                variable=variable.name,
            )
            return coefficient
        if not is_finite_real(coefficient):
            raise ObjectiveError("Objective coefficient must be a finite real number")
        if self.objective is None:
            raise EmptyModelError("Model has no objective")
        retained = [
            term
            for term in self.objective.terms
            if not (len(term.variables) == 1 and term.variables[0] is variable)
        ]
        if float(coefficient) != 0.0:
            retained.append(Term(float(coefficient), [variable]))
        replacement = Expression(terms=retained, constant=self.objective.constant)
        if not any(declared is variable for declared in self._declared_variables):
            ordered = self.variables()
            position = next(index for index, item in enumerate(ordered) if item is variable)
            self._declared_variables.extend(ordered[len(self._declared_variables) : position + 1])
        self.objective = replacement
        return float(coefficient)

    def _resolve_constraint_item(self, constraint):
        if isinstance(constraint, numbers.Integral) and not isinstance(constraint, bool):
            index = int(constraint)
            if 0 <= index < len(self.constraints):
                return index, self.constraints[index]
        elif isinstance(constraint, str):
            for index, item in enumerate(self.constraints):
                if self._constraint_name(index, item) == constraint:
                    return index, item
        raise ModelValidationError(f"Unknown model constraint {constraint!r}")

    def set_constraint_rhs(self, constraint, value):
        """Replace a relational constraint's scalar right-hand side."""

        from .parameters import Parameter

        index, item = self._resolve_constraint_item(constraint)
        if isinstance(value, Parameter):
            self.bind_parameter(value, target="constraint_rhs", constraint=index)
            return value
        if not is_finite_real(value):
            raise ConstraintValidationError("Constraint RHS must be a finite real number")
        relation = item["constraint"]
        if isinstance(relation, Indicator):
            relation = relation.constraint
        if not isinstance(relation, (Equal, LessEqual, GreaterEqual)):
            raise ConstraintValidationError("RHS updates require a relational constraint")
        relation.rhs = Expression(constant=float(value))
        relation._penalty_cache = None
        return float(value)

    def set_variable_bounds(self, variable_or_name, *, lower_bound=None, upper_bound=None):
        """Validate and update one variable's bounds without rebuilding the model."""

        variable = self._resolve_variable(variable_or_name)
        if variable.var_type in {"BINARY", "SPIN"}:
            raise ModelValidationError(f"{variable.var_type} variables have fixed domains")
        current_lower = getattr(variable, "lower_bound", None)
        current_upper = getattr(variable, "upper_bound", None)
        lower = current_lower if lower_bound is None else lower_bound
        upper = current_upper if upper_bound is None else upper_bound
        from .variables import Continuous, Fraction, Integer, SemiContinuous, SemiInteger

        constructors = {
            "INTEGER": lambda: Integer(variable.name, lower, upper),
            "CONTINUOUS": lambda: Continuous(variable.name, lower, upper),
            "SEMI_CONTINUOUS": lambda: SemiContinuous(variable.name, lower, upper),
            "SEMI_INTEGER": lambda: SemiInteger(variable.name, lower, upper),
            "FRACTIONAL": lambda: Fraction(
                variable.name, lower, upper, variable.precision_decimals
            ),
        }
        if variable.var_type not in constructors:
            raise ModelValidationError(f"Bounds updates do not support {variable.var_type}")
        validated = constructors[variable.var_type]()
        variable.lower_bound = validated.lower_bound
        variable.upper_bound = validated.upper_bound
        return variable

    def add_parameter(self, parameter):
        """Register a named parameter and return it."""

        from .parameters import Parameter

        if not isinstance(parameter, Parameter):
            raise ModelValidationError("add_parameter expects a Parameter")
        existing = self.parameters.get(parameter.name)
        if existing is not None and existing is not parameter:
            raise ModelValidationError(f"Parameter '{parameter.name}' is already registered")
        self.parameters[parameter.name] = parameter
        return parameter

    def bind_parameter(
        self,
        parameter,
        *,
        target,
        variable=None,
        constraint=None,
    ):
        """Bind a Parameter to one supported mutable model field."""

        from .parameters import Parameter

        registered_here = False
        if isinstance(parameter, Parameter):
            registered_here = parameter.name not in self.parameters
            self.add_parameter(parameter)
            name = parameter.name
        elif isinstance(parameter, str) and parameter in self.parameters:
            name = parameter
        else:
            raise ModelValidationError(f"Unknown parameter {parameter!r}")
        try:
            binding = self._parameter_binding(
                name, target=target, variable=variable, constraint=constraint
            )
            self._apply_parameter_binding(binding)
        except Exception:
            if registered_here:
                del self.parameters[name]
            raise
        if binding not in self._parameter_bindings:
            self._parameter_bindings.append(binding)
        return dict(binding)

    def _parameter_binding(self, parameter, *, target, variable=None, constraint=None):
        """Validate a binding's references without applying its parameter value."""

        if not isinstance(parameter, str) or parameter not in self.parameters:
            raise ModelValidationError(f"Unknown parameter {parameter!r}")
        if not isinstance(target, str) or target not in {
            "objective_coefficient",
            "constraint_rhs",
            "lower_bound",
            "upper_bound",
        }:
            raise ModelValidationError(f"Unsupported parameter target {target!r}")
        binding = {"parameter": parameter, "target": target}
        if target == "constraint_rhs":
            index, item = self._resolve_constraint_item(constraint)
            relation = item["constraint"]
            if isinstance(relation, Indicator):
                relation = relation.constraint
            if not isinstance(relation, (Equal, LessEqual, GreaterEqual)):
                raise ConstraintValidationError("RHS updates require a relational constraint")
            binding["constraint"] = index
        else:
            resolved = self._resolve_variable(variable)
            if target in {"lower_bound", "upper_bound"} and resolved.var_type not in {
                "INTEGER",
                "CONTINUOUS",
                "SEMI_CONTINUOUS",
                "SEMI_INTEGER",
                "FRACTIONAL",
            }:
                raise ModelValidationError(f"Bounds updates do not support {resolved.var_type}")
            binding["variable"] = resolved.name
        return binding

    def _apply_parameter_binding(self, binding):
        value = self.parameters[binding["parameter"]].value
        target = binding["target"]
        if target == "objective_coefficient":
            self.set_objective_coefficient(binding["variable"], value)
        elif target == "constraint_rhs":
            self.set_constraint_rhs(binding["constraint"], value)
        elif target == "lower_bound":
            self.set_variable_bounds(binding["variable"], lower_bound=value)
        else:
            self.set_variable_bounds(binding["variable"], upper_bound=value)

    def set_parameter(self, parameter_or_name, value):
        """Set a parameter and atomically apply all of its registered bindings."""

        name = parameter_or_name.name if hasattr(parameter_or_name, "name") else parameter_or_name
        if name not in self.parameters:
            raise ModelValidationError(f"Unknown parameter {name!r}")
        parameter = self.parameters[name]
        previous = parameter.value
        objective_before = self.objective
        declared_count = len(self._declared_variables)
        constraint_states = []
        for item in self.constraints:
            relation = item["constraint"]
            if isinstance(relation, Indicator):
                relation = relation.constraint
            if isinstance(relation, (Equal, LessEqual, GreaterEqual)):
                constraint_states.append(
                    (relation, relation.rhs, getattr(relation, "_penalty_cache", None))
                )
        variable_states = [
            (
                variable,
                getattr(variable, "lower_bound", None),
                getattr(variable, "upper_bound", None),
            )
            for variable in self.variables()
            if hasattr(variable, "lower_bound")
        ]
        parameter.set(value)
        try:
            bindings = [
                binding for binding in self._parameter_bindings if binding["parameter"] == name
            ]
            for binding in bindings:
                if binding["target"] not in {"lower_bound", "upper_bound"}:
                    self._apply_parameter_binding(binding)
            bound_variables = {
                binding["variable"]
                for binding in bindings
                if binding["target"] in {"lower_bound", "upper_bound"}
            }
            for variable_name in bound_variables:
                variable = self._resolve_variable(variable_name)
                targets = {
                    binding["target"]
                    for binding in bindings
                    if binding.get("variable") == variable_name
                }
                self.set_variable_bounds(
                    variable,
                    lower_bound=value if "lower_bound" in targets else variable.lower_bound,
                    upper_bound=value if "upper_bound" in targets else variable.upper_bound,
                )
        except Exception:
            parameter.set(previous)
            self.objective = objective_before
            del self._declared_variables[declared_count:]
            for relation, rhs, penalty_cache in constraint_states:
                relation.rhs = rhs
                relation._penalty_cache = penalty_cache
            for variable, lower_bound, upper_bound in variable_states:
                variable.lower_bound = lower_bound
                variable.upper_bound = upper_bound
            raise
        return parameter.value

    def add_constraint(self, constraint, penalty_weight=None, label=None):
        """Add and return a supported constraint."""

        if not isinstance(
            constraint,
            (
                Equal,
                LessEqual,
                GreaterEqual,
                Indicator,
                LogicalConstraint,
                CustomPenalty,
                SOS1,
                SOS2,
            ),
        ):
            raise ConstraintValidationError(
                "Expected a relational, indicator, logical, custom-penalty, SOS1, or SOS2 constraint"
            )
        if label is not None and (
            not isinstance(label, str)
            or not label
            or any(character in label for character in "\x00\r\n")
        ):
            raise ConstraintValidationError(
                "Constraint label must be a non-empty single-line string"
            )
        effective_label = label
        if effective_label is None and isinstance(constraint, CustomPenalty):
            effective_label = constraint.label
        new_name = effective_label or f"c{len(self.constraints)}"
        if any(
            self._constraint_name(index, item) == new_name
            for index, item in enumerate(self.constraints)
        ):
            raise ConstraintValidationError(f"Constraint name '{new_name}' is already in use")

        if isinstance(constraint, (SOS1, SOS2, Indicator)):
            if penalty_weight is not None:
                raise ConstraintValidationError(
                    "SOS and indicator constraints do not accept penalty weights"
                )
            if isinstance(constraint, (SOS1, SOS2)):
                if any(
                    isinstance(item["constraint"], (SOS1, SOS2))
                    and item["constraint"].name == constraint.name
                    for item in self.constraints
                ):
                    raise ConstraintValidationError(
                        f"SOS name '{constraint.name}' is already in use"
                    )
            weight = None
        else:
            if penalty_weight is not None:
                if not is_finite_real(penalty_weight) or penalty_weight <= 0:
                    raise ConstraintValidationError(
                        "Penalty weight must be a finite positive number or None for automatic selection"
                    )
                weight = float(penalty_weight)
            else:
                weight = None

        self.constraints.append(
            {"constraint": constraint, "weight": weight, "label": effective_label}
        )
        return constraint

    def add_piecewise_linear(self, source, output, breakpoints, values, *, name=None):
        """Add an exact SOS2 convex-combination interpolation relationship."""

        from .piecewise import PiecewiseLinear
        from .variables import Continuous

        if not isinstance(source, Variable) or not isinstance(output, Variable):
            raise ModelValidationError(
                "piecewise-linear source and output must be decision variables"
            )
        if source is output:
            raise ModelValidationError("piecewise-linear source and output must be distinct")
        if source.name == output.name:
            raise ModelValidationError(
                "piecewise-linear source and output must have distinct variable names"
            )
        if name is not None and (
            not isinstance(name, str)
            or not name
            or any(character in name for character in "\x00\r\n")
        ):
            raise ModelValidationError("piecewise-linear name must be a non-empty single line")
        try:
            raw_breakpoints = tuple(breakpoints)
            raw_values = tuple(values)
        except TypeError as exc:
            raise ModelValidationError(
                "piecewise-linear breakpoints and values must be finite real sequences"
            ) from exc
        if any(not is_finite_real(value) for value in raw_breakpoints + raw_values):
            raise ModelValidationError(
                "piecewise-linear breakpoints and values must be finite real sequences"
            )
        x_coordinates = tuple(float(value) for value in raw_breakpoints)
        y_coordinates = tuple(float(value) for value in raw_values)
        if len(x_coordinates) < 2 or len(x_coordinates) != len(y_coordinates):
            raise ModelValidationError(
                "piecewise-linear interpolation requires equally sized breakpoint/value "
                "sequences with at least two points"
            )
        if any(right <= left for left, right in zip(x_coordinates, x_coordinates[1:])):
            raise ModelValidationError("piecewise-linear breakpoints must be strictly increasing")

        current_variables = self.variables()
        current_by_name = {variable.name: variable for variable in current_variables}
        for role, variable in (("source", source), ("output", output)):
            existing = current_by_name.get(variable.name)
            if existing is not None and existing is not variable:
                raise ModelValidationError(
                    f"piecewise-linear {role} variable name '{variable.name}' is already used "
                    "by a distinct model variable"
                )

        used_variable_names = set(current_by_name) | {source.name, output.name}
        stem = self._unique_generated_name(name or "__qpiai_pwl", used_variable_names)
        weights = []
        for index in range(len(x_coordinates)):
            weight_name = self._unique_generated_name(f"{stem}_lambda_{index}", used_variable_names)
            used_variable_names.add(weight_name)
            weight = Continuous(weight_name, 0.0, 1.0)
            weight._is_auxiliary = True
            weight._persist_in_ir = True
            weights.append(weight)
        constraint_names = {
            self._constraint_name(index, item) for index, item in enumerate(self.constraints)
        }

        def unique_constraint_name(suffix):
            candidate = f"{stem}_{suffix}"
            if candidate not in constraint_names:
                constraint_names.add(candidate)
                return candidate
            position = 1
            while f"{candidate}_{position}" in constraint_names:
                position += 1
            result = f"{candidate}_{position}"
            constraint_names.add(result)
            return result

        staged_constraints = [
            (quicksum(weights) == 1.0, unique_constraint_name("weights_sum")),
            (
                source == quicksum(x * weight for x, weight in zip(x_coordinates, weights)),
                unique_constraint_name("source_link"),
            ),
            (
                output == quicksum(y * weight for y, weight in zip(y_coordinates, weights)),
                unique_constraint_name("output_link"),
            ),
        ]
        sos_name = unique_constraint_name("sos2")
        staged_constraints.append((SOS2(sos_name, weights, weights=x_coordinates), sos_name))
        relationship = PiecewiseLinear(
            name=stem,
            source=source,
            output=output,
            breakpoints=x_coordinates,
            values=y_coordinates,
            weights=tuple(weights),
        )
        metadata = {
            "stage": "modelling",
            "transformation": "piecewise_linear_sos2",
            "name": stem,
            "source_variable": source.name,
            "output_variable": output.name,
            "breakpoints": list(x_coordinates),
            "values": list(y_coordinates),
            "weight_variables": [weight.name for weight in weights],
        }

        declared_count = len(self._declared_variables)
        constraint_count = len(self.constraints)
        metadata_count = len(self.transformation_metadata)
        try:
            self.add_variables(weights)
            for constraint, label in staged_constraints:
                self.add_constraint(constraint, label=label)
            self.transformation_metadata.append(metadata)
        except Exception:
            del self._declared_variables[declared_count:]
            del self.constraints[constraint_count:]
            del self.transformation_metadata[metadata_count:]
            raise
        return relationship

    def variables(self):
        """Return model variables in first-use order."""

        ordered = []
        seen = set()
        source_names = set()
        for variable in self._variable_objects():
            if id(variable) in seen:
                continue
            seen.add(id(variable))
            ordered.append(variable)
            if not getattr(variable, "_is_auxiliary", False):
                if variable.name in source_names:
                    raise ModelValidationError(
                        f"Model '{self.name}' contains distinct variables with duplicate name "
                        f"'{variable.name}'"
                    )
                source_names.add(variable.name)
        reserved_names = {variable.name for variable in ordered}

        def rename_auxiliary(variable, used_names):
            old_name = variable.name
            variable.name = self._unique_generated_name(old_name, used_names)
            if not getattr(variable, "_persist_in_ir", False):
                return
            records = []
            for record in self.transformation_metadata:
                if record.get("transformation") == "piecewise_linear_sos2":
                    record = dict(record)
                    record["weight_variables"] = [
                        variable.name if name == old_name else name
                        for name in record["weight_variables"]
                    ]
                    for field in ("source_variable", "output_variable"):
                        if record[field] == old_name:
                            record[field] = variable.name
                records.append(record)
            self.transformation_metadata = records

        for variable in ordered:
            if not getattr(variable, "_is_auxiliary", False):
                continue
            if variable.name in source_names:
                rename_auxiliary(variable, reserved_names | source_names)
            source_names.add(variable.name)
        return ordered

    def _variable_objects(self):
        """Traverse source objects without changing their names."""

        yield from self._declared_variables
        if self.objective is not None:
            for term in self.objective.terms:
                yield from term.variables
        for item in self.constraints:
            constraint = item["constraint"]
            if isinstance(constraint, (Equal, LessEqual, GreaterEqual)):
                expressions = (constraint.lhs, constraint.rhs)
            elif isinstance(constraint, (LogicalConstraint, Indicator)):
                yield from constraint.variables()
                continue
            elif isinstance(constraint, CustomPenalty):
                expressions = (constraint.expression,)
            elif isinstance(constraint, (SOS1, SOS2)):
                yield from constraint.variables
                continue
            else:
                continue
            for expression in expressions:
                for term in expression.terms:
                    yield from term.variables

    def _require_model(self):
        if self.objective is None:
            raise EmptyModelError("Model has no objective")
        return self.variables()

    def validate(self, target=None):
        """Validate model structure and return its detected problem class."""

        variables = self._require_model()
        target = target.upper() if isinstance(target, str) else target
        if target not in {None, "LP", "MILP", "MIP", "QP", "QUBO", "HUBO", "ISING"}:
            raise ModelValidationError(f"Unknown validation target {target!r}")

        objective_degree = self.objective.degree(raise_on_higher_order=False)
        all_binary = all(variable.var_type == "BINARY" for variable in variables)
        all_spin = bool(variables) and all(variable.var_type == "SPIN" for variable in variables)

        def is_linear_constraint(constraint):
            if isinstance(constraint, (SOS1, SOS2)):
                return True
            relation = constraint.constraint if isinstance(constraint, Indicator) else constraint
            return (
                isinstance(relation, (Equal, LessEqual, GreaterEqual))
                and relation.lhs.degree(raise_on_higher_order=False) <= 1
                and relation.rhs.degree(raise_on_higher_order=False) <= 1
            )

        linear = objective_degree <= 1 and all(
            is_linear_constraint(item["constraint"]) for item in self.constraints
        )

        if target in {"LP", "MILP", "MIP"}:
            self._validate_linear()
            return self._linear_problem_type(variables)
        if target == "QP":
            self._validate_qp()
            return "QP"
        if target == "QUBO":
            self._validate_qubo_source()
            return "QUBO"
        if target == "HUBO":
            self._validate_hubo_source()
            return "HUBO"
        if target == "ISING":
            self._validate_ising_source()
            return "ISING"

        if all_spin:
            self._validate_ising_source()
            return "ISING"
        if all_binary and objective_degree > 2:
            self._validate_hubo_source()
            return "HUBO"
        if all_binary and objective_degree == 2 and not self.constraints:
            return "QUBO"
        if linear:
            self._validate_linear()
            return self._linear_problem_type(variables)
        if objective_degree == 2 and all(
            variable.var_type == "CONTINUOUS" for variable in variables
        ):
            self._validate_qp()
            return "QP"
        if all_binary and objective_degree <= 2:
            self._validate_qubo_source()
            return "QUBO"
        raise ModelValidationError(
            "Model is neither a supported linear model nor a binary polynomial model"
        )

    def _validate_qp(self):
        variables = self._require_model()
        if self.objective.degree(raise_on_higher_order=False) != 2:
            raise ModelValidationError("QP requires a quadratic objective")
        noncontinuous = [
            variable.name for variable in variables if variable.var_type != "CONTINUOUS"
        ]
        if noncontinuous:
            raise ModelValidationError(
                "Continuous QP does not support discrete variables: " + ", ".join(noncontinuous)
            )
        for index, item in enumerate(self.constraints):
            constraint = item["constraint"]
            if isinstance(constraint, (SOS1, SOS2, Indicator)):
                raise ModelValidationError(
                    f"Continuous QP does not support {type(constraint).__name__} constraints"
                )
            if not isinstance(constraint, (Equal, LessEqual, GreaterEqual)):
                raise ModelValidationError(
                    f"Continuous QP constraint {self._constraint_name(index, item)} must be linear"
                )
            if (
                max(
                    constraint.lhs.degree(raise_on_higher_order=False),
                    constraint.rhs.degree(raise_on_higher_order=False),
                )
                > 1
            ):
                raise ModelValidationError("Quadratic constraints are not supported")

    def _validate_qubo_source(self):
        variables = self._require_model()
        spin_names = [variable.name for variable in variables if variable.var_type == "SPIN"]
        if spin_names:
            raise InvalidQUBOError(
                "Spin variables require native Ising compilation or an explicit Ising-to-QUBO "
                f"conversion: {', '.join(spin_names)}"
            )
        if self.objective.degree(raise_on_higher_order=False) > 2:
            raise InvalidQUBOError("QUBO compilation accepts objective degree at most two")
        if any(
            isinstance(item["constraint"], (SOS1, SOS2, Indicator)) for item in self.constraints
        ):
            raise InvalidQUBOError("SOS and indicator constraints require LP/MILP export")

    def _validate_hubo_source(self):
        variables = self._require_model()
        spin_names = [variable.name for variable in variables if variable.var_type == "SPIN"]
        if spin_names:
            raise ModelValidationError(
                f"Spin variables require native Ising compilation: {', '.join(spin_names)}"
            )
        if any(
            isinstance(item["constraint"], (SOS1, SOS2, Indicator)) for item in self.constraints
        ):
            raise ModelValidationError("SOS and indicator constraints require LP/MILP export")

    def _validate_ising_source(self):
        variables = self._require_model()
        non_spin = [variable.name for variable in variables if variable.var_type != "SPIN"]
        if non_spin:
            raise ModelValidationError(
                f"Native Ising models may contain only Spin variables; found {', '.join(non_spin)}"
            )
        if self.constraints:
            raise ModelValidationError(
                "Native Ising models are unconstrained; encode penalties directly in the objective"
            )
        for term in self.objective.terms:
            parity = {}
            for variable in term.variables:
                identity = id(variable)
                parity[identity] = not parity.get(identity, False)
            if sum(parity.values()) > 2:
                raise ModelValidationError(
                    "Native Ising objectives support at most two distinct odd-power spins per term"
                )

    def _validate_linear(self):
        variables = self._require_model()
        if self.objective.degree(raise_on_higher_order=False) > 1:
            raise InvalidLinearModelError(f"Nonlinear objective: {self.objective}")
        for index, item in enumerate(self.constraints):
            constraint = item["constraint"]
            if isinstance(constraint, (SOS1, SOS2)):
                continue
            relation = constraint.constraint if isinstance(constraint, Indicator) else constraint
            if not isinstance(relation, (Equal, LessEqual, GreaterEqual)):
                raise InvalidLinearModelError(
                    f"Constraint {self._constraint_name(index, item)} is not a native linear constraint"
                )
            if (
                relation.lhs.degree(raise_on_higher_order=False) > 1
                or relation.rhs.degree(raise_on_higher_order=False) > 1
            ):
                raise InvalidLinearModelError(
                    f"Constraint {self._constraint_name(index, item)} is nonlinear"
                )
        supported = {
            "BINARY",
            "INTEGER",
            "CONTINUOUS",
            "SEMI_CONTINUOUS",
            "SEMI_INTEGER",
            "FRACTIONAL",
            "MIXED",
        }
        unsupported = [
            variable.name for variable in variables if variable.var_type not in supported
        ]
        if unsupported:
            raise InvalidLinearModelError(
                f"Unsupported linear variable types: {', '.join(unsupported)}"
            )

    def compile(self, target=None, **options):
        """Validate and package an executable problem, preserving source decoding.

        The default target is the model's detected class. LP and MILP share
        MILPProblem. Continuous QP retains its canonical Model representation.
        """

        with self._compilation_transaction():
            if target is None:
                target = self.validate()
                if target == "MIP":
                    target = "MILP"
            if not isinstance(target, str):
                raise ModelValidationError("target must be a problem-class string")
            target = target.upper()
            if target == "QUBO":
                from ..opt_solvers.qubo import QUBOProblem

                return QUBOProblem.from_model(self, **options)
            if target == "HUBO":
                from ..opt_solvers.hubo import HUBOProblem

                return HUBOProblem.from_model(self, **options)
            if target == "ISING":
                from ..opt_solvers.ising import IsingProblem

                return IsingProblem.from_model(self, **options)
            if target in {"LP", "MILP"}:
                from ..opt_solvers.milp import MILPProblem

                unknown = set(options) - {"config"}
                if unknown:
                    raise TypeError(
                        "LP/MILP compilation does not accept options: " + ", ".join(sorted(unknown))
                    )
                self.validate(target=target)
                compiled = MILPProblem.from_model(self, **options)
                if target == "LP" and compiled.problem_type != "LP":
                    raise InvalidLinearModelError("LP compilation cannot retain discrete variables")
                return compiled
            if target == "QP":
                if options:
                    raise TypeError(
                        "QP compilation does not accept options: " + ", ".join(sorted(options))
                    )
                self.to_qp_matrix()
                return deepcopy(self)
            raise ModelValidationError(f"Unknown compilation target {target!r}")

    def estimate_penalty_weight(self, constraint, **compile_options):
        """Return the compiler's automatic weight without modifying this model.

        Supply a constraint object, or an existing constraint's label or index.
        The same encoding options as to_qubo_triplets apply.
        A redundant constraint needs no penalty and returns zero.
        """

        if isinstance(constraint, CustomPenalty):
            raise ConstraintValidationError(
                "CustomPenalty contributions do not have an automatic hard-constraint weight"
            )
        if not isinstance(constraint, (str, numbers.Integral)):
            snapshot, copied_constraint = deepcopy((self, constraint))
            matches = [
                index
                for index, item in enumerate(snapshot.constraints)
                if item["constraint"] is copied_constraint
            ]
            if matches:
                index = matches[0]
            else:
                snapshot.add_constraint(copied_constraint)
                index = len(snapshot.constraints) - 1
        else:
            index, item = self._resolve_constraint_item(constraint)
            snapshot = deepcopy(self)
        if isinstance(snapshot.constraints[index]["constraint"], CustomPenalty):
            raise ConstraintValidationError(
                "CustomPenalty contributions do not have an automatic hard-constraint weight"
            )
        for item in snapshot.constraints:
            if not isinstance(item["constraint"], CustomPenalty):
                item["weight"] = None
        if self.objective.degree() > 2:
            snapshot.to_hubo_dict(**compile_options)
        else:
            snapshot.to_qubo_triplets(**compile_options)
        label = snapshot._constraint_name(index, snapshot.constraints[index])
        for record in snapshot.transformation_metadata:
            if record.get("stage") == "constraint_encoding" and record.get("constraint") == label:
                return record.get("penalty_weight", 0.0)
        raise ConstraintValidationError(
            "CustomPenalty contributions do not have an automatic hard-constraint weight"
        )

    def to_ising_dict(self, **compile_options):
        """Return indexed Ising coefficients, offset, source sense, and names."""

        with self._compilation_transaction():
            converted = self._to_ising(**compile_options)
            sign = -1.0 if self.sense == "MAX" else 1.0
            return {
                "problem_type": "ISING",
                "num_variables": converted.num_variables,
                "sense": self.sense,
                "offset": sign * converted.offset,
                "variable_names": converted.variable_names,
                "linear": {index: sign * value for index, value in converted.linear.items()},
                "quadratic": {key: sign * value for key, value in converted.quadratic.items()},
            }

    def to_qubo_triplets(
        self,
        *,
        matrix_format="symmetric",
        continuous_bits=None,
        continuous_precision=None,
        max_bits=20,
        strict_penalties=False,
    ):
        """Compile a constrained quadratic source model into compact QUBO triplets.

        ``matrix_format="symmetric"`` is the platform-compatible default.
        ``matrix_format="upper"`` returns direct polynomial coefficients.
        Insufficient explicit hard-constraint weights warn by default;
        ``strict_penalties=True`` rejects them instead.
        """

        strict_penalties = _validate_boolean_option(strict_penalties, "strict_penalties")
        from .formats.qubo import (
            _format_canonical_upper_triplets,
            normalize_qubo_matrix_format,
        )

        matrix_format = normalize_qubo_matrix_format(matrix_format)
        with self._compilation_transaction():
            self._validate_qubo_source()
            expression, compiled_variables = self._compile_binary_polynomial(
                maximum_degree=2,
                continuous_bits=continuous_bits,
                continuous_precision=continuous_precision,
                max_bits=max_bits,
                strict_penalties=strict_penalties,
            )
            rows, cols, coefficients, constant = self._lower_quadratic(expression)
            rows, cols, coefficients = self._aggregate_qubo(rows, cols, coefficients)
            rows, cols = self._compact_qubo_indices(compiled_variables, rows, cols)
            swap = rows > cols
            rows[swap], cols[swap] = cols[swap].copy(), rows[swap].copy()
            rows, cols, coefficients = self._aggregate_qubo(rows, cols, coefficients)
            rows, cols, coefficients = _format_canonical_upper_triplets(
                rows,
                cols,
                coefficients,
                matrix_format=matrix_format,
            )
            return rows, cols, coefficients, constant

    def to_qubo_dict(self, *, matrix_format="symmetric", **compile_options):
        """Return a QUBO dictionary and offset in the selected matrix format."""

        rows, cols, coefficients, offset = self.to_qubo_triplets(
            matrix_format=matrix_format,
            **compile_options,
        )
        return (
            {
                (int(row), int(col)): float(coefficient)
                for row, col, coefficient in zip(rows, cols, coefficients)
            },
            offset,
        )

    def _to_ising(self, **compile_options):
        """Compile a native spin model or convert a binary model to Ising."""

        variables = self._require_model()
        if variables and all(variable.var_type == "SPIN" for variable in variables):
            if compile_options:
                unknown = ", ".join(sorted(compile_options))
                raise TypeError(f"Native Ising compilation does not accept QUBO options: {unknown}")
            return self._compile_ising()
        if any(variable.var_type == "SPIN" for variable in variables):
            self._validate_ising_source()

        from .converters import qubo_to_ising

        qubo, offset = self.to_qubo_dict(matrix_format="upper", **compile_options)
        ising_convert = qubo_to_ising(
            qubo,
            num_variables=len(self.qubo_variable_names),
            constant=offset,
            variable_names=self.qubo_variable_names,
        )
        converted = ising_convert.with_original_sense(self.sense)
        self.transformation_metadata.append(
            {
                "stage": "representation_conversion",
                **converted.transformations[0],
            }
        )
        return converted

    def _compile_ising(self):
        """Compile a native spin objective to canonical ``h``, ``J``, and offset."""

        self._validate_ising_source()
        variables = self.variables()
        index_by_identity = {id(variable): index for index, variable in enumerate(variables)}
        linear_contributions = []
        quadratic_contributions = []
        sign = -1.0 if self.sense == "MAX" else 1.0
        offset_contributions = [sign * float(self.objective.constant)]

        for term in self.objective.terms:
            parity = {}
            for variable in term.variables:
                identity = id(variable)
                parity[identity] = not parity.get(identity, False)
            active = sorted(
                (index_by_identity[identity] for identity, odd in parity.items() if odd)
            )
            coefficient = sign * float(term.coefficient)
            if not active:
                offset_contributions.append(coefficient)
            elif len(active) == 1:
                linear_contributions.append((active[0], coefficient))
            elif len(active) == 2:
                quadratic_contributions.append(((active[0], active[1]), coefficient))
            else:
                raise ModelValidationError(
                    "Native Ising objectives support at most quadratic spin interactions"
                )

        try:
            linear = fsum_by_key(linear_contributions)
            quadratic = fsum_by_key(quadratic_contributions)
            offset = finite_fsum(offset_contributions)
        except OverflowError as exc:
            raise ModelCompilationError("Ising coefficient aggregation overflowed") from exc

        from .converters import _IsingData

        result = _IsingData(
            linear={key: value for key, value in linear.items() if value != 0.0},
            quadratic={key: value for key, value in quadratic.items() if value != 0.0},
            offset=offset,
            num_variables=len(variables),
            variable_names=tuple(variable.name for variable in variables),
            original_sense=self.sense,
        )
        self.transformation_metadata = [
            item for item in self.transformation_metadata if item.get("stage") == "modelling"
        ]
        self.transformation_metadata.append(
            {
                "stage": "representation_compilation",
                "transformation": "native_ising",
                "source_sense": self.sense,
                "target_sense": "MIN",
                "offset": result.offset,
                "variable_mapping": result.index_to_variable,
            }
        )
        return result

    def to_hubo_dict(
        self,
        *,
        continuous_bits=None,
        continuous_precision=None,
        max_bits=20,
        strict_penalties=False,
    ):
        """Compile a constrained polynomial source model to a minimization HUBO.

        Insufficient explicit hard-constraint weights warn by default;
        ``strict_penalties=True`` rejects them instead.
        """

        strict_penalties = _validate_boolean_option(strict_penalties, "strict_penalties")
        with self._compilation_transaction():
            self._validate_hubo_source()
            expression, compiled_variables = self._compile_binary_polynomial(
                maximum_degree=None,
                continuous_bits=continuous_bits,
                continuous_precision=continuous_precision,
                max_bits=max_bits,
                strict_penalties=strict_penalties,
            )
            from .formats.hubo import expression_to_polynomial_dict

            polynomial = expression_to_polynomial_dict(expression)
            self.hubo_variable_names = sorted(variable.name for variable in compiled_variables)
            return polynomial

    @contextmanager
    def _compilation_transaction(self):
        """Restore the last successful compilation state after an error."""

        state = (
            self.encoding_map,
            self.transformation_metadata,
            self.qubo_variable_names,
            self.qubo_variable_indices,
            self.hubo_variable_names,
        )
        auxiliary_names = {
            id(variable): (variable, variable.name)
            for variable in self._variable_objects()
            if getattr(variable, "_is_auxiliary", False)
        }
        try:
            yield
        except BaseException:
            (
                self.encoding_map,
                self.transformation_metadata,
                self.qubo_variable_names,
                self.qubo_variable_indices,
                self.hubo_variable_names,
            ) = state
            for variable, name in auxiliary_names.values():
                variable.name = name
            raise

    def _compile_binary_polynomial(
        self,
        *,
        maximum_degree,
        continuous_bits,
        continuous_precision,
        max_bits,
        strict_penalties,
    ):
        self.transformation_metadata = [
            item for item in self.transformation_metadata if item.get("stage") == "modelling"
        ]
        mapping = _encode_variables(
            self.variables(),
            continuous_bits=continuous_bits,
            continuous_precision=continuous_precision,
            max_bits=max_bits,
        )
        self.encoding_map = {
            variable.name: encoded.encoding
            for variable, encoded in mapping.items()
            if hasattr(encoded, "encoding")
        }
        auxiliary_records = []
        for source_name, encoding in self.encoding_map.items():
            self.transformation_metadata.append(
                {
                    "stage": "domain_encoding",
                    "source_variable": source_name,
                    "encoded_variables": [binary.name for binary in encoding["binaries"]],
                    "offset": float(encoding["offset"]),
                    "weights": [float(weight) for weight in encoding["weights"]],
                }
            )
            auxiliary_records.append(
                (self.transformation_metadata[-1], "encoded_variables", encoding["binaries"])
            )

        objective = dispatch_encoders(self.objective, mapping)
        if (
            maximum_degree is not None
            and objective.degree(raise_on_higher_order=False) > maximum_degree
        ):
            raise InvalidQUBOError(
                f"Encoded objective degree {objective.degree(raise_on_higher_order=False)} "
                f"exceeds QUBO degree {maximum_degree}"
            )
        normalized_objective = -objective if self.sense == "MAX" else objective
        self.transformation_metadata.append(
            {
                "stage": "objective_normalization",
                "source_sense": self.sense,
                "target_sense": "MIN",
                "sign": -1.0 if self.sense == "MAX" else 1.0,
                "source_offset": float(self.objective.constant),
            }
        )
        hard_constraints = []
        base_contributions = [normalized_objective]
        for index, item in enumerate(self.constraints):
            constraint = item["constraint"]
            if isinstance(constraint, (SOS1, SOS2)):
                raise ModelCompilationError(
                    "SOS constraints cannot be converted to binary penalties"
                )

            penalty, minimum_violation = self._expanded_penalty(
                constraint,
                mapping,
                token=index,
            )
            if (
                maximum_degree is not None
                and penalty.degree(raise_on_higher_order=False) > maximum_degree
            ):
                raise InvalidQUBOError(
                    f"Constraint {self._constraint_name(index, item)} produces degree "
                    f"{penalty.degree(raise_on_higher_order=False)}, exceeding QUBO"
                )
            if not penalty.terms and penalty.constant == 0.0:
                self.transformation_metadata.append(
                    {
                        "stage": "constraint_encoding",
                        "constraint": self._constraint_name(index, item),
                        "rewrite": "redundant",
                        "auxiliary_variables": [],
                    }
                )
                continue

            weight = item["weight"]
            if isinstance(constraint, CustomPenalty):
                weight = 1.0 if weight is None else weight
                base_contributions.append(weight * penalty)
            else:
                hard_constraints.append((index, item, penalty, minimum_violation))

        base_energy = quicksum(base_contributions)
        try:
            objective_range = math.fsum(abs(term.coefficient) for term in base_energy.terms)
        except OverflowError as exc:
            raise ModelCompilationError(
                "Objective coefficient range overflowed; rescale the model"
            ) from exc
        contributions = [base_energy]
        for index, item, penalty, minimum_violation in hard_constraints:
            weight = item["weight"]
            if minimum_violation is not None:
                ratio = objective_range / minimum_violation
                if not math.isfinite(ratio):
                    raise ModelCompilationError(
                        "Required penalty weight is not finite; rescale the model"
                    )
                required = max(
                    1.0,
                    ratio + max(1.0, abs(ratio) * 1e-6),
                )
                if not math.isfinite(required):
                    raise ModelCompilationError(
                        "Required penalty weight is not finite; rescale the model"
                    )
                if weight is None:
                    weight = required
                elif weight < required:
                    if strict_penalties:
                        raise ConstraintValidationError(
                            f"Penalty weight {weight:g} for {self._constraint_name(index, item)} "
                            f"is insufficient; use at least {required:.12g} or automatic selection"
                        )
                    warnings.warn(
                        "Selected penalty may be insufficient. "
                        f"Recommended penalty weight: {required}.",
                        UserWarning,
                        stacklevel=3,
                    )
            elif weight is None:
                weight = 1.0
            contributions.append(weight * penalty)
            auxiliaries = list(
                {
                    variable.index: variable
                    for term in penalty.terms
                    for variable in term.variables
                    if getattr(variable, "_is_auxiliary", False)
                }.values()
            )
            self.transformation_metadata.append(
                {
                    "stage": "constraint_encoding",
                    "constraint": self._constraint_name(index, item),
                    "rewrite": getattr(penalty, "_qpiai_rewrite", "generic_exact_penalty"),
                    "penalty_weight": float(weight),
                    "minimum_positive_penalty": minimum_violation,
                    "auxiliary_variables": sorted(variable.name for variable in auxiliaries),
                }
            )
            auxiliary_records.append(
                (self.transformation_metadata[-1], "auxiliary_variables", auxiliaries)
            )

        result = quicksum(contributions)
        if (
            maximum_degree is not None
            and result.degree(raise_on_higher_order=False) > maximum_degree
        ):
            raise InvalidQUBOError("Compiled expression is not quadratic")
        compiled_variables = self._compiled_binary_variables(result)
        for record, field, variables in auxiliary_records:
            names = [variable.name for variable in variables]
            record[field] = sorted(names) if field == "auxiliary_variables" else names
        return result, compiled_variables

    def _expanded_penalty(self, constraint, mapping, token):
        if isinstance(constraint, (Equal, LessEqual, GreaterEqual)):
            lhs = dispatch_encoders(constraint.lhs, mapping)
            rhs = dispatch_encoders(constraint.rhs, mapping)
            if isinstance(constraint, Equal):
                source_difference = constraint.lhs - constraint.rhs
                difference = lhs - rhs
                minimum, maximum = expression_bounds(difference)
                if minimum > 0.0 or maximum < 0.0:
                    raise ConstraintValidationError(
                        f"Equality constraint c{token} cannot reach zero over "
                        "the encoded variable bounds"
                    )
                step = expression_lattice_step(source_difference, mapping)
                penalty = difference**2
            else:
                source_difference = constraint._standard_difference()
                expanded = type(constraint)(lhs, rhs)
                expanded._aux_name_token = token
                difference = expanded._standard_difference()
                specialized = self._specialized_binary_inequality_penalty(difference)
                if specialized is not None:
                    penalty, rewrite = specialized
                    penalty._qpiai_rewrite = rewrite
                    return penalty, 1.0
                step = expression_lattice_step(source_difference, mapping)
                expanded._lattice_step = step
                penalty = expanded.to_penalty()
            minimum_penalty = step * step
            try:
                magnitude = math.fsum(
                    [abs(float(penalty.constant))]
                    + [abs(float(term.coefficient)) for term in penalty.terms]
                )
            except OverflowError as exc:
                raise EncodingError(
                    f"Constraint c{token} produces an unrepresentable QUBO penalty; "
                    "rescale its coefficients"
                ) from exc
            roundoff_bound = math.ulp(magnitude) * max(1, len(penalty.terms) + 1)
            if minimum_penalty <= roundoff_bound:
                raise EncodingError(
                    f"Constraint c{token} has violations below reliable QUBO energy "
                    "resolution; rescale or round its coefficients"
                )
            return penalty, minimum_penalty
        if isinstance(constraint, LogicalConstraint):
            return constraint.to_penalty(), 1.0
        if isinstance(constraint, CustomPenalty):
            return dispatch_encoders(constraint.expression, mapping), None
        raise ModelCompilationError(f"Unsupported constraint type {type(constraint).__name__}")

    @staticmethod
    def _specialized_binary_inequality_penalty(difference):
        """Recognize exact, slack-free penalties for common binary inequalities."""

        coefficients, constant = linear_coefficients(difference)
        if len(coefficients) < 2 or any(variable.var_type != "BINARY" for variable in coefficients):
            return None

        positives = [(variable, value) for variable, value in coefficients.items() if value > 0.0]
        negatives = [(variable, value) for variable, value in coefficients.items() if value < 0.0]
        if (
            len(coefficients) == 2
            and len(positives) == 1
            and len(negatives) == 1
            and constant == 0.0
            and positives[0][1] == -negatives[0][1]
        ):
            antecedent = positives[0][0]
            consequent = negatives[0][0]
            return antecedent * (1 - consequent), "binary_implication"

        values = list(coefficients.values())
        scale = values[0]
        if scale > 0.0 and all(value == scale for value in values) and constant == -scale:
            variables = list(coefficients)
            penalty = quicksum(
                variables[left] * variables[right]
                for left in range(len(variables))
                for right in range(left + 1, len(variables))
            )
            return penalty, "binary_at_most_one"

        if (
            scale < 0.0
            and all(value == scale for value in values)
            and constant == -scale * (len(values) - 1)
        ):
            variables = list(coefficients)
            penalty = quicksum(
                (1 - variables[left]) * (1 - variables[right])
                for left in range(len(variables))
                for right in range(left + 1, len(variables))
            )
            return penalty, "binary_at_least_n_minus_one"
        return None

    def transformations(self):
        """Return a detached record of the latest modelling/compilation transformations."""

        import copy

        return copy.deepcopy(self.transformation_metadata)

    def to_ommx(self):
        """Convert this model to an optional OMMX instance."""

        from .ommx import to_ommx

        return to_ommx(self)

    @classmethod
    def from_ommx(cls, instance):
        """Construct a model from the supported OMMX subset."""

        from .ommx import from_ommx

        return from_ommx(instance)

    def _lower_quadratic(self, expression):
        if expression._can_vectorize():
            coefficients = expression._coeffs
            indices = expression._var_indices
            if not len(coefficients):
                return (
                    np.empty(0, dtype=np.int64),
                    np.empty(0, dtype=np.int64),
                    np.empty(0, dtype=np.float64),
                    float(expression.constant),
                )
            first = indices[:, 0]
            second = indices[:, 1]
            constant_mask = (first < 0) & (second < 0)
            try:
                constant = finite_fsum(
                    [float(expression.constant), *coefficients[constant_mask].tolist()]
                )
            except OverflowError as exc:
                raise ModelCompilationError(
                    "QUBO constant aggregation overflowed; rescale the model"
                ) from exc
            active = ~constant_mask
            first = first[active]
            second = second[active]
            coefficients = coefficients[active].astype(np.float64, copy=True)
            linear = (second < 0) | (first == second)
            rows = first.astype(np.int64, copy=True)
            cols = np.where(linear, first, second).astype(np.int64, copy=False)
            swap = rows > cols
            rows[swap], cols[swap] = cols[swap].copy(), rows[swap].copy()
            return rows, cols, coefficients, constant

        rows = []
        cols = []
        coefficients = []
        constant_contributions = [float(expression.constant)]
        for term in expression.terms:
            indices = sorted({variable.index for variable in term.variables})
            if len(indices) == 0:
                constant_contributions.append(term.coefficient)
            elif len(indices) == 1:
                rows.append(indices[0])
                cols.append(indices[0])
                coefficients.append(term.coefficient)
            elif len(indices) == 2:
                rows.append(indices[0])
                cols.append(indices[1])
                coefficients.append(term.coefficient)
            else:
                raise InvalidQUBOError(
                    f"Higher-order term remains after QUBO compilation: {term.variables}"
                )
        try:
            constant = finite_fsum(constant_contributions)
        except OverflowError as exc:
            raise ModelCompilationError(
                "QUBO constant aggregation overflowed; rescale the model"
            ) from exc
        return (
            np.asarray(rows, dtype=np.int64),
            np.asarray(cols, dtype=np.int64),
            np.asarray(coefficients, dtype=np.float64),
            constant,
        )

    @staticmethod
    def _aggregate_qubo(rows, cols, coefficients):
        if not len(rows):
            return rows, cols, coefficients
        pairs = np.rec.fromarrays((rows, cols), names=("row", "col"))
        order = np.argsort(pairs, order=("row", "col"))
        sorted_rows = rows[order]
        sorted_cols = cols[order]
        sorted_coefficients = coefficients[order]
        starts = np.r_[
            0,
            np.flatnonzero(
                (sorted_rows[1:] != sorted_rows[:-1]) | (sorted_cols[1:] != sorted_cols[:-1])
            )
            + 1,
        ]
        stops = np.r_[starts[1:], len(sorted_coefficients)]
        totals = sorted_coefficients[starts].copy()
        try:
            for index in np.flatnonzero(stops - starts > 1):
                totals[index] = finite_fsum(sorted_coefficients[starts[index] : stops[index]])
        except OverflowError as exc:
            raise ModelCompilationError(
                "QUBO coefficient aggregation overflowed; rescale the model"
            ) from exc
        nonzero = totals != 0.0
        return sorted_rows[starts][nonzero], sorted_cols[starts][nonzero], totals[nonzero]

    def _compiled_binary_variables(self, expression):
        identities = []
        variables = {}
        source_variables = self.variables()
        for term in expression.terms:
            for variable in term.variables:
                if variable.var_type != "BINARY":
                    raise InvalidQUBOError(f"Variable '{variable.name}' was not encoded to binary")
                if variable.index not in variables:
                    variables[variable.index] = variable
                    identities.append(variable.index)
        for variable in source_variables:
            compiled_variables = (
                (variable,)
                if variable.var_type == "BINARY"
                else self.encoding_map.get(variable.name, {}).get("binaries", ())
            )
            for compiled in compiled_variables:
                if compiled.index not in variables:
                    variables[compiled.index] = compiled
                    identities.append(compiled.index)
        source_names = [
            variables[identity].name
            for identity in identities
            if not getattr(variables[identity], "_is_auxiliary", False)
        ]
        if len(set(source_names)) != len(source_names):
            raise ModelCompilationError(
                "Compiled polynomial contains duplicate source variable names"
            )
        used_names = set(source_names)
        reserved_names = used_names | {variable.name for variable in source_variables}
        reserved_names.update(variable.name for variable in variables.values())
        for identity in identities:
            variable = variables[identity]
            if not getattr(variable, "_is_auxiliary", False):
                continue
            if variable.name in used_names:
                variable.name = self._unique_generated_name(variable.name, reserved_names)
            used_names.add(variable.name)
        return [variables[identity] for identity in identities]

    def _compact_qubo_indices(self, variables, rows, cols):
        local = {variable.index: index for index, variable in enumerate(variables)}
        self.qubo_variable_names = [variable.name for variable in variables]
        self.qubo_variable_indices = {
            name: index for index, name in enumerate(self.qubo_variable_names)
        }
        if len(rows):
            rows = np.fromiter((local[int(row)] for row in rows), dtype=np.int64, count=len(rows))
            cols = np.fromiter((local[int(col)] for col in cols), dtype=np.int64, count=len(cols))
        return rows, cols

    def qubo_index(self, variable_or_name):
        """Return a variable's index from the most recent QUBO compilation."""

        name = variable_or_name.name if isinstance(variable_or_name, Variable) else variable_or_name
        if name not in self.qubo_variable_indices:
            raise KeyError(f"Variable '{name}' is not in the latest QUBO compilation")
        return self.qubo_variable_indices[name]

    def _decode_binary_sample(self, sample, variable_names, *, representation):
        indices = {name: index for index, name in enumerate(variable_names)}
        if isinstance(sample, dict):
            binary_values = {}
            for name, index in indices.items():
                if name in sample and index in sample:
                    if sample[name] != sample[index]:
                        raise ValueError(
                            f"{representation} sample contains conflicting values for '{name}' "
                            f"and index {index}"
                        )
                    binary_values[name] = sample[name]
                elif name in sample:
                    binary_values[name] = sample[name]
                elif index in sample:
                    binary_values[name] = sample[index]
                else:
                    raise KeyError(f"Missing {representation} value for '{name}'")
        else:
            values = list(sample)
            if len(values) != len(variable_names):
                raise ValueError(
                    f"Expected {len(variable_names)} {representation} values, got {len(values)}"
                )
            binary_values = dict(zip(variable_names, values))
        if any(value not in (0, 1, False, True) for value in binary_values.values()):
            raise ValueError(f"{representation} samples must contain binary values")

        decoded = {}
        for variable in self.variables():
            if getattr(variable, "_is_auxiliary", False):
                continue
            if variable.var_type == "BINARY":
                if variable.name in binary_values:
                    decoded[variable.name] = int(binary_values[variable.name])
            elif variable.name in self.encoding_map:
                encoding = self.encoding_map[variable.name]
                try:
                    decoded[variable.name] = finite_fsum(
                        [
                            float(encoding["offset"]),
                            *(
                                weight * int(binary_values[binary.name])
                                for weight, binary in zip(
                                    encoding["weights"],
                                    encoding["binaries"],
                                )
                            ),
                        ]
                    )
                except OverflowError as exc:
                    raise ModelCompilationError(
                        f"Decoded value for '{variable.name}' overflowed"
                    ) from exc
        return decoded

    def decode_qubo_sample(self, sample):
        """Decode a sample from the most recent QUBO compilation."""

        return self._decode_binary_sample(
            sample,
            self.qubo_variable_names,
            representation="QUBO",
        )

    def decode_hubo_sample(self, sample):
        """Decode a sample from the most recent HUBO compilation."""

        return self._decode_binary_sample(
            sample,
            self.hubo_variable_names,
            representation="HUBO",
        )

    def evaluate_objective(self, values):
        """Evaluate the source objective for a complete variable assignment."""

        self._require_model()
        return self.objective.evaluate(values)

    @staticmethod
    def _distance_to_interval(value, lower, upper):
        if value < lower:
            return lower - value
        if value > upper:
            return value - upper
        return 0.0

    @classmethod
    def _variable_domain_violation(cls, variable, raw_value):
        if not is_finite_real(raw_value, allow_bool=variable.var_type == "BINARY"):
            return math.inf
        value = float(raw_value)

        if variable.var_type == "BINARY":
            return min(abs(value), abs(value - 1.0))
        if variable.var_type == "SPIN":
            return min(abs(value + 1.0), abs(value - 1.0))
        if variable.var_type == "CONTINUOUS":
            return cls._distance_to_interval(
                value,
                variable.lower_bound,
                variable.upper_bound,
            )
        if variable.var_type == "INTEGER":
            if value < variable.lower_bound:
                return variable.lower_bound - value
            if value > variable.upper_bound:
                return value - variable.upper_bound
            return abs(value - round(value))
        if variable.var_type == "SEMI_CONTINUOUS":
            return min(
                abs(value),
                cls._distance_to_interval(
                    value,
                    variable.lower_bound,
                    variable.upper_bound,
                ),
            )
        if variable.var_type == "SEMI_INTEGER":
            closest_integer = min(
                max(round(value), variable.lower_bound),
                variable.upper_bound,
            )
            return min(abs(value), abs(value - closest_integer))
        if variable.var_type == "FRACTIONAL":
            lower_units = _fixed_point_units(
                variable.lower_bound,
                variable.precision_decimals,
                variable_name=variable.name,
                error_type=ModelValidationError,
            )
            upper_units = _fixed_point_units(
                variable.upper_bound,
                variable.precision_decimals,
                variable_name=variable.name,
                error_type=ModelValidationError,
            )
            if value < variable.lower_bound:
                return variable.lower_bound - value
            if value > variable.upper_bound:
                return value - variable.upper_bound
            scale = 10**variable.precision_decimals
            closest_units = min(
                max(round(value * scale), lower_units),
                upper_units,
            )
            return abs(value - closest_units / scale)
        if variable.var_type == "MIXED":
            lower, upper = variable.continuous_range
            distances = [
                cls._distance_to_interval(value, lower, upper),
                *(abs(value - candidate) for candidate in variable.valid_discrete_values),
            ]
            return min(distances)
        raise ModelValidationError(
            f"Cannot check the domain of unsupported variable type {variable.var_type!r}"
        )

    def _domain_violations(self, values, tolerance):
        violations = {}
        evaluable = True
        piecewise_weight_names = {
            name
            for record in self.transformation_metadata
            if isinstance(record, Mapping)
            and record.get("transformation") == "piecewise_linear_sos2"
            and isinstance(record.get("weight_variables"), (list, tuple))
            for name in record["weight_variables"]
            if isinstance(name, str)
        }
        for variable in self.variables():
            has_object_key = variable in values
            has_name_key = variable.name in values
            if getattr(variable, "_is_auxiliary", False):
                if variable.name not in piecewise_weight_names or not (
                    has_object_key or has_name_key
                ):
                    continue
            key = f"variable:{variable.name}"
            if not has_object_key and not has_name_key:
                violations[key] = math.inf
                evaluable = False
                continue
            raw_value = values[variable] if has_object_key else values[variable.name]
            if has_object_key and has_name_key:
                named_value = values[variable.name]
                if not is_finite_real(
                    raw_value,
                    allow_bool=variable.var_type == "BINARY",
                ) or not is_finite_real(
                    named_value,
                    allow_bool=variable.var_type == "BINARY",
                ):
                    violations[key] = math.inf
                    evaluable = False
                    continue
                if float(raw_value) != float(named_value):
                    violations[key] = math.inf
                    evaluable = False
                    continue
            if not is_finite_real(raw_value, allow_bool=variable.var_type == "BINARY"):
                violations[key] = math.inf
                evaluable = False
                continue
            violation = self._variable_domain_violation(variable, raw_value)
            if violation > tolerance:
                violations[key] = float(violation)
        return violations, evaluable

    def constraint_violations(
        self,
        values,
        *,
        tolerance=DEFAULT_FEASIBILITY_TOLERANCE,
    ):
        """Return non-negative violations for variable domains and hard constraints."""

        if not isinstance(values, Mapping):
            raise ValueError("Constraint values must be a mapping keyed by variable or name")
        if not is_finite_real(tolerance) or tolerance < 0:
            raise ValueError("tolerance must be a finite non-negative number")
        tolerance = float(tolerance)
        violations, evaluable = self._domain_violations(values, tolerance)
        if not evaluable:
            return violations

        evaluation_values = dict(values)
        variables_by_name = {variable.name: variable for variable in self.variables()}
        domain_names = {f"variable:{name}" for name in variables_by_name}
        constraint_names = {
            self._constraint_name(index, item) for index, item in enumerate(self.constraints)
        }
        for record in self.transformation_metadata:
            if not isinstance(record, Mapping):
                raise ModelValidationError("Model transformation metadata is invalid")
            if record.get("transformation") != "piecewise_linear_sos2":
                continue
            try:
                source = variables_by_name[record["source_variable"]]
                output = variables_by_name[record["output_variable"]]
                breakpoints = tuple(float(value) for value in record["breakpoints"])
                point_values = tuple(float(value) for value in record["values"])
                weights = [variables_by_name[name] for name in record["weight_variables"]]
            except (KeyError, TypeError, ValueError) as exc:
                raise ModelValidationError(
                    "Piecewise-linear transformation metadata is invalid"
                ) from exc
            if (
                source is output
                or any(variable is source for variable in weights)
                or any(variable is output for variable in weights)
                or len({id(variable) for variable in weights}) != len(weights)
                or len(breakpoints) < 2
                or len(breakpoints) != len(point_values)
                or len(breakpoints) != len(weights)
                or any(not math.isfinite(value) for value in breakpoints + point_values)
                or any(right <= left for left, right in zip(breakpoints, breakpoints[1:]))
                or any(
                    variable.var_type != "CONTINUOUS"
                    or variable.lower_bound != 0.0
                    or variable.upper_bound != 1.0
                    for variable in weights
                )
            ):
                raise ModelValidationError("Piecewise-linear transformation metadata is invalid")
            try:
                source_value = float(
                    evaluation_values[source]
                    if source in evaluation_values
                    else evaluation_values[source.name]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ModelValidationError(
                    "Piecewise-linear source assignment is missing or invalid"
                ) from exc
            if all(
                variable in evaluation_values or variable.name in evaluation_values
                for variable in weights
            ):
                continue
            derived_weights = [0.0] * len(breakpoints)
            if source_value <= breakpoints[0]:
                derived_weights[0] = 1.0
            elif source_value >= breakpoints[-1]:
                derived_weights[-1] = 1.0
            else:
                right = bisect_right(breakpoints, source_value)
                left = right - 1
                numerator = source_value - breakpoints[left]
                denominator = breakpoints[right] - breakpoints[left]
                if not math.isfinite(numerator) or not math.isfinite(denominator):
                    raise NumericalStabilityError(
                        "Piecewise-linear weight inference is not representable; "
                        "rescale the breakpoints or supply explicit weights"
                    )
                fraction = numerator / denominator
                if not 0.0 <= fraction < 1.0 or (fraction == 0.0 and numerator != 0.0):
                    raise NumericalStabilityError(
                        "Piecewise-linear weight inference loses an interior weight; "
                        "rescale the breakpoints or supply explicit weights"
                    )
                derived_weights[left] = 1.0 - fraction
                derived_weights[right] = fraction
            for variable, value in zip(weights, derived_weights):
                if variable not in evaluation_values and variable.name not in evaluation_values:
                    evaluation_values[variable.name] = value

        def value_of(variable):
            if variable in evaluation_values:
                return evaluation_values[variable]
            if variable.name in evaluation_values:
                return evaluation_values[variable.name]
            raise KeyError(f"Missing value for variable '{variable.name}'")

        def relational_violation(relation, name):
            try:
                difference_expression = relation.lhs - relation.rhs
                difference = difference_expression.evaluate(evaluation_values)
            except InvalidExpressionError as exc:
                raise NumericalStabilityError(
                    f"Constraint '{name}' cannot be evaluated within binary64 range; "
                    "rescale the expression or assignment"
                ) from exc

            activity = difference - float(difference_expression.constant)
            if not math.isfinite(activity):
                contributions = []
                try:
                    for term in difference_expression.to_terms():
                        product = float(term.coefficient)
                        for variable in term.variables:
                            product = _finite_product(
                                product, float(value_of(variable)), "Constraint activity"
                            )
                        contributions.append(product)
                    try:
                        activity = finite_fsum(contributions)
                    except OverflowError:
                        activity = sum(
                            (Fraction.from_float(value) for value in contributions),
                            start=Fraction(),
                        )
                except InvalidExpressionError as exc:
                    raise NumericalStabilityError(
                        f"Constraint '{name}' activity cannot be evaluated within binary64 range; "
                        "rescale the expression or assignment"
                    ) from exc

            if isinstance(relation, Equal):
                violation = abs(difference)
            elif isinstance(relation, LessEqual):
                violation = max(0.0, difference)
            else:
                violation = max(0.0, -difference)
            if isinstance(activity, Fraction):
                scale = max(
                    Fraction(1),
                    abs(activity),
                    abs(Fraction.from_float(float(difference_expression.constant))),
                )
            else:
                scale = max(1.0, abs(activity), abs(float(difference_expression.constant)))
            return violation, scale

        for index, item in enumerate(self.constraints):
            constraint = item["constraint"]
            name = self._constraint_name(index, item)
            if name in domain_names:
                name = f"constraint:{name}"
                while name in domain_names or name in constraint_names or name in violations:
                    name = f"constraint:{name}"
            if isinstance(constraint, CustomPenalty):
                continue
            if isinstance(constraint, Indicator):
                trigger_value = value_of(constraint.binary)
                if trigger_value not in (0, 1, False, True):
                    violation, scale = math.inf, 1.0
                elif int(trigger_value) != constraint.active_value:
                    violation, scale = 0.0, 1.0
                else:
                    violation, scale = relational_violation(constraint.constraint, name)
            elif isinstance(constraint, (Equal, LessEqual, GreaterEqual)):
                violation, scale = relational_violation(constraint, name)
            elif isinstance(constraint, LogicalConstraint):
                public_variables = (
                    (constraint.x, constraint.y, constraint.z)
                    if hasattr(constraint, "z")
                    else (constraint.x, constraint.y)
                )
                binary_values = [value_of(variable) for variable in public_variables]
                if any(value not in (0, 1, False, True) for value in binary_values):
                    violation = math.inf
                elif hasattr(constraint, "z"):
                    x_value, y_value, z_value = map(int, binary_values[:3])
                    if constraint.__class__.__name__ == "AND":
                        expected = x_value & y_value
                    elif constraint.__class__.__name__ == "OR":
                        expected = x_value | y_value
                    else:
                        expected = x_value ^ y_value
                    violation = float(z_value != expected)
                else:
                    x_value, y_value = map(int, binary_values[:2])
                    violation = float(y_value != 1 - x_value)
                scale = 1.0
            elif isinstance(constraint, (SOS1, SOS2)):
                active = [
                    position
                    for position, variable in enumerate(constraint.variables)
                    if abs(float(value_of(variable))) > tolerance
                ]
                if isinstance(constraint, SOS1):
                    violation = float(max(0, len(active) - 1))
                else:
                    ordered_positions = sorted(
                        range(len(constraint.variables)),
                        key=lambda position: constraint.weights[position],
                    )
                    rank = {position: order for order, position in enumerate(ordered_positions)}
                    active_ranks = sorted(rank[position] for position in active)
                    valid = len(active_ranks) <= 1 or (
                        len(active_ranks) == 2 and active_ranks[1] - active_ranks[0] == 1
                    )
                    violation = 0.0 if valid else 1.0
                scale = 1.0
            else:
                raise ModelValidationError(
                    f"Unsupported constraint type {type(constraint).__name__}"
                )
            if isinstance(scale, Fraction):
                within_tolerance = (
                    math.isfinite(violation)
                    and Fraction.from_float(float(violation))
                    <= Fraction.from_float(tolerance) * scale
                )
            else:
                within_tolerance = violation <= tolerance * scale
            violations[name] = 0.0 if within_tolerance else float(violation)
        return violations

    def is_feasible(self, values, *, tolerance=DEFAULT_FEASIBILITY_TOLERANCE):
        """Return whether all constraints are satisfied within ``tolerance``."""

        return all(
            violation == 0.0
            for violation in self.constraint_violations(values, tolerance=tolerance).values()
        )

    def to_lp_matrix(
        self,
        for_milp=True,
        *,
        sparse_matrix=True,
        _objective_override=None,
        _skip_validation=False,
    ):
        """Return the sparse linear matrix representation used by local solvers."""

        for_milp = _validate_boolean_option(for_milp, "for_milp")
        sparse_matrix = _validate_boolean_option(sparse_matrix, "sparse_matrix")
        if not _skip_validation:
            self._validate_linear()
        original_variables = self.variables()
        specs = [self._variable_spec(variable) for variable in original_variables]
        rows = []
        constraint_names = set()

        for index, item in enumerate(self.constraints):
            constraint = item["constraint"]
            if isinstance(constraint, (SOS1, SOS2)):
                continue
            name = self._constraint_name(index, item)
            if isinstance(constraint, Indicator):
                self._append_indicator_constraint(rows, constraint_names, name, constraint)
            else:
                self._append_linear_constraint(rows, constraint_names, name, constraint)

        self._add_domain_reformulations(original_variables, specs, rows, constraint_names)
        if for_milp:
            self._add_sos_reformulations(specs, rows, constraint_names)

        names = [spec["name"] for spec in specs]
        if len(names) != len(set(names)):
            raise InvalidLinearModelError(
                "Generated linear model contains duplicate variable names"
            )
        index_by_name = {name: index for index, name in enumerate(names)}

        objective_expression = (
            self.objective if _objective_override is None else _objective_override
        )
        objective_coefficients, objective_constant = self._linear_components(objective_expression)
        objective = np.zeros(len(names), dtype=np.float64)
        for name, coefficient in objective_coefficients.items():
            objective[index_by_name[name]] = coefficient

        row_indices = []
        column_indices = []
        data = []
        row_lower = []
        row_upper = []
        structured = []
        for row_index, row in enumerate(rows):
            for name, coefficient in row["coefficients"].items():
                if coefficient:
                    row_indices.append(row_index)
                    column_indices.append(index_by_name[name])
                    data.append(coefficient)
            row_lower.append(row["lower"])
            row_upper.append(row["upper"])
            structured.append(dict(row))

        matrix = sparse.csr_matrix(
            (data, (row_indices, column_indices)),
            shape=(len(rows), len(names)),
            dtype=np.float64,
        )
        if not sparse_matrix:
            matrix = matrix.toarray()

        native_sos = []
        if not for_milp:
            for item in self.constraints:
                constraint = item["constraint"]
                if isinstance(constraint, (SOS1, SOS2)):
                    native_sos.append(
                        {
                            "name": constraint.name,
                            "type": "S1" if isinstance(constraint, SOS1) else "S2",
                            "variables": [variable.name for variable in constraint.variables],
                            "weights": list(constraint.weights),
                        }
                    )

        return {
            "name": self.name,
            "problem_type": self._linear_problem_type(original_variables),
            "solver": "milp",
            "format": "matrix",
            "sense": self.sense.lower(),
            "variable_names": names,
            "objective": objective,
            "objective_coefficients": objective_coefficients,
            "objective_offset": float(objective_constant),
            "constraint_names": [row["name"] for row in rows],
            "constraint_matrix": matrix,
            "row_lower": np.asarray(row_lower, dtype=np.float64),
            "row_upper": np.asarray(row_upper, dtype=np.float64),
            "constraints": structured,
            "lower_bounds": np.asarray([spec["lower"] for spec in specs], dtype=np.float64),
            "upper_bounds": np.asarray([spec["upper"] for spec in specs], dtype=np.float64),
            "integrality": np.asarray([spec["integrality"] for spec in specs], dtype=np.int32),
            "variable_types": {spec["name"]: spec["kind"] for spec in specs},
            "sos": native_sos,
            "sos_reformulated": bool(for_milp),
        }

    def to_qp_matrix(self, *, sparse_matrix=True):
        """Return a continuous-QP matrix after source-sense curvature validation.

        The objective convention is ``0.5 * x.T @ Q @ x + c.T @ x + offset``.
        """

        sparse_matrix = _validate_boolean_option(sparse_matrix, "sparse_matrix")
        self._validate_qp()
        variables = self.variables()
        index_by_identity = {id(variable): index for index, variable in enumerate(variables)}
        linear_terms = []
        hessian_contributions = []
        for term in self.objective.terms:
            factors = term.variables
            coefficient = float(term.coefficient)
            if len(factors) == 1:
                linear_terms.append(Term(coefficient, factors))
            elif len(factors) == 2:
                left = index_by_identity[id(factors[0])]
                right = index_by_identity[id(factors[1])]
                if left == right:
                    hessian_contributions.append(((left, left), 2.0 * coefficient))
                else:
                    for key in ((left, right), (right, left)):
                        hessian_contributions.append((key, coefficient))
            else:
                raise ModelValidationError("Continuous QP objective must be at most quadratic")
        try:
            hessian_entries = fsum_by_key(hessian_contributions)
        except OverflowError as exc:
            raise ModelValidationError("QP Hessian coefficient aggregation overflowed") from exc
        count = len(variables)
        rows = [key[0] for key in hessian_entries]
        columns = [key[1] for key in hessian_entries]
        values = list(hessian_entries.values())
        hessian = sparse.csc_matrix((values, (rows, columns)), shape=(count, count))
        extreme_eigenvalue, tolerance = _qp_curvature(hessian, self.sense)
        if extreme_eigenvalue < -tolerance:
            direction = "positive semidefinite" if self.sense == "MIN" else "negative semidefinite"
            raise ModelValidationError(
                f"Nonconvex continuous QP: a {self.sense} objective requires a {direction} "
                f"Hessian; extreme signed eigenvalue is "
                f"{extreme_eigenvalue:.12g}"
            )
        if extreme_eigenvalue < 0.0:
            raise NumericalStabilityError(
                "Continuous QP curvature is too close to the numerical boundary to verify; "
                "rescale or reformulate the objective"
            )
        linear_objective = Expression(
            terms=linear_terms,
            constant=float(self.objective.constant),
        )
        data = self.to_lp_matrix(
            for_milp=False,
            sparse_matrix=sparse_matrix,
            _objective_override=linear_objective,
            _skip_validation=True,
        )
        data.update(
            {
                "problem_type": "QP",
                "solver": "highs",
                "objective_hessian": hessian if sparse_matrix else hessian.toarray(),
                "objective_convention": "0.5 * x.T @ Q @ x + c.T @ x + offset",
                "convex": True,
                "global_optimum_if_optimal": True,
                "hessian_extreme_eigenvalue": extreme_eigenvalue,
                "convexity_tolerance": tolerance,
            }
        )
        self.transformation_metadata = [
            item for item in self.transformation_metadata if item.get("stage") == "modelling"
        ]
        self.transformation_metadata.append(
            {
                "stage": "objective_matrix",
                "source_sense": self.sense,
                "target_convention": data["objective_convention"],
                "source_offset": float(self.objective.constant),
                "hessian_nonzeros": int(hessian.nnz),
                "curvature_validated": True,
                "convexity_tolerance": tolerance,
            }
        )
        return data

    def _append_linear_constraint(self, rows, names, name, constraint):
        coefficients, constant = self._linear_components(constraint.lhs - constraint.rhs)
        rhs = -constant
        symbol = constraint._symbol()
        if symbol == "==":
            lower = upper = rhs
            sense = "="
        elif symbol == "<=":
            lower, upper, sense = -math.inf, rhs, "<="
        elif symbol == ">=":
            lower, upper, sense = rhs, math.inf, ">="
        else:
            raise InvalidLinearModelError(f"Unsupported constraint sense {symbol!r}")
        self._append_row(rows, names, name, coefficients, sense, rhs, lower, upper)

    def _append_indicator_constraint(self, rows, names, name, indicator):
        relation = indicator.constraint
        difference = relation.lhs - relation.rhs
        try:
            expression_bounds(difference)
        except ConstraintValidationError as exc:
            raise InvalidLinearModelError(
                f"Indicator constraint '{name}' requires finite expression bounds"
            ) from exc

        sides = (
            [(LessEqual(difference, 0), f"{name}_le"), (GreaterEqual(difference, 0), f"{name}_ge")]
            if isinstance(relation, Equal)
            else [(relation, name)]
        )
        for side, side_name in sides:
            side_name = self._available_generated_name(side_name, names)
            side_difference = side.lhs - side.rhs
            if isinstance(side, LessEqual):
                upper_bound = max(0.0, expression_bounds(side_difference)[1])
                if indicator.active_value == 1:
                    reformulated = side_difference + upper_bound * indicator.binary <= upper_bound
                    self._validate_indicator_active_reformulation(
                        side_name, side_difference, reformulated, indicator.binary
                    )
                else:
                    reformulated = side_difference - upper_bound * indicator.binary <= 0
            else:
                lower_bound = min(0.0, expression_bounds(side_difference)[0])
                if indicator.active_value == 1:
                    reformulated = side_difference + lower_bound * indicator.binary >= lower_bound
                    self._validate_indicator_active_reformulation(
                        side_name, side_difference, reformulated, indicator.binary
                    )
                else:
                    reformulated = side_difference - lower_bound * indicator.binary >= 0
            self._append_linear_constraint(rows, names, side_name, reformulated)

    def _validate_indicator_active_reformulation(
        self, name, source_difference, reformulated, binary
    ):
        """Reject an active indicator row whose big-M cancellation exceeds tolerance."""

        source_coefficients, source_constant = self._linear_components(source_difference)
        generated_difference = reformulated.lhs - reformulated.rhs
        generated_coefficients, generated_constant = self._linear_components(generated_difference)
        source_binary = source_coefficients.get(binary.name, 0.0)
        generated_binary = generated_coefficients.get(binary.name, 0.0)
        source_rhs = float(-source_constant - source_binary)
        generated_rhs = float(-generated_constant - generated_binary)
        error = abs(generated_rhs - source_rhs)
        scale = max(1.0, abs(source_rhs), abs(generated_rhs))
        if error > DEFAULT_FEASIBILITY_TOLERANCE * scale:
            raise NumericalStabilityError(
                f"Indicator constraint '{name}' cannot be represented reliably with its "
                f"current bounds (active-row error {error:.6g}); tighten the bounded "
                "variables or rescale the model"
            )

    @staticmethod
    def _append_row(rows, names, name, coefficients, sense, rhs, lower, upper):
        if name in names:
            raise InvalidLinearModelError(f"Constraint name '{name}' is already in use")
        names.add(name)
        rows.append(
            {
                "name": name,
                "sense": sense,
                "rhs": float(rhs),
                "lower": float(lower),
                "upper": float(upper),
                "coefficients": {
                    variable: float(coefficient)
                    for variable, coefficient in coefficients.items()
                    if coefficient != 0.0
                },
                "is_sos": False,
            }
        )

    def _add_domain_reformulations(self, variables, specs, rows, names):
        existing_names = {spec["name"] for spec in specs}
        for variable in variables:
            if variable.var_type == "FRACTIONAL":
                lower_units = _fixed_point_units(
                    variable.lower_bound,
                    variable.precision_decimals,
                    variable_name=variable.name,
                    error_type=InvalidLinearModelError,
                )
                scale = 10 ** int(variable.precision_decimals)
                upper_units = _fixed_point_units(
                    variable.upper_bound,
                    variable.precision_decimals,
                    variable_name=variable.name,
                    error_type=InvalidLinearModelError,
                )
                if lower_units > upper_units:
                    raise InvalidLinearModelError(
                        f"Fraction variable '{variable.name}' has an invalid fixed-point domain"
                    )
                integer_name = self._unique_generated_name(
                    f"__qpiai_fraction_units_{variable.name}", existing_names
                )
                specs.append(
                    {
                        "name": integer_name,
                        "lower": lower_units,
                        "upper": upper_units,
                        "kind": "INTEGER",
                        "integrality": 1,
                    }
                )
                self._append_row(
                    rows,
                    names,
                    self._available_generated_name(f"__qpiai_fraction_link_{variable.name}", names),
                    {variable.name: float(scale), integer_name: -1.0},
                    "=",
                    0.0,
                    0.0,
                    0.0,
                )
            elif variable.var_type == "MIXED":
                lower, upper = variable.continuous_range
                continuous_name = self._unique_generated_name(
                    f"__qpiai_mixed_cont_{variable.name}", existing_names
                )
                selector_name = self._unique_generated_name(
                    f"__qpiai_mixed_select_cont_{variable.name}", existing_names
                )
                discrete_names = [
                    self._unique_generated_name(
                        f"__qpiai_mixed_select_{variable.name}_{index}", existing_names
                    )
                    for index in range(len(variable.valid_discrete_values))
                ]
                specs.extend(
                    [
                        {
                            "name": continuous_name,
                            "lower": min(0.0, lower),
                            "upper": max(0.0, upper),
                            "kind": "CONTINUOUS",
                            "integrality": 0,
                        },
                        {
                            "name": selector_name,
                            "lower": 0.0,
                            "upper": 1.0,
                            "kind": "BINARY",
                            "integrality": 1,
                        },
                    ]
                )
                specs.extend(
                    {
                        "name": name,
                        "lower": 0.0,
                        "upper": 1.0,
                        "kind": "BINARY",
                        "integrality": 1,
                    }
                    for name in discrete_names
                )
                selector_coefficients = {selector_name: 1.0}
                selector_coefficients.update({name: 1.0 for name in discrete_names})
                self._append_row(
                    rows,
                    names,
                    self._available_generated_name(f"__qpiai_mixed_choose_{variable.name}", names),
                    selector_coefficients,
                    "=",
                    1.0,
                    1.0,
                    1.0,
                )
                self._append_row(
                    rows,
                    names,
                    self._available_generated_name(f"__qpiai_mixed_upper_{variable.name}", names),
                    {continuous_name: 1.0, selector_name: -upper},
                    "<=",
                    0.0,
                    -math.inf,
                    0.0,
                )
                self._append_row(
                    rows,
                    names,
                    self._available_generated_name(f"__qpiai_mixed_lower_{variable.name}", names),
                    {continuous_name: 1.0, selector_name: -lower},
                    ">=",
                    0.0,
                    0.0,
                    math.inf,
                )
                link = {variable.name: 1.0, continuous_name: -1.0}
                link.update(
                    {
                        name: -value
                        for name, value in zip(discrete_names, variable.valid_discrete_values)
                    }
                )
                self._append_row(
                    rows,
                    names,
                    self._available_generated_name(f"__qpiai_mixed_link_{variable.name}", names),
                    link,
                    "=",
                    0.0,
                    0.0,
                    0.0,
                )

    def _add_sos_reformulations(self, specs, rows, names):
        spec_by_name = {spec["name"]: spec for spec in specs}
        existing_names = set(spec_by_name)
        for item in self.constraints:
            constraint = item["constraint"]
            if isinstance(constraint, SOS1):
                selectors = []
                for variable in constraint.variables:
                    spec = spec_by_name[variable.name]
                    self._require_finite_sos_bounds(constraint.name, spec)
                    selector = self._unique_generated_name(
                        f"__qpiai_sos1_{constraint.name}_{variable.name}", existing_names
                    )
                    selectors.append(selector)
                    new_spec = {
                        "name": selector,
                        "lower": 0.0,
                        "upper": 1.0,
                        "kind": "BINARY",
                        "integrality": 1,
                    }
                    specs.append(new_spec)
                    spec_by_name[selector] = new_spec
                    self._add_activation_rows(
                        rows, names, constraint.name, variable.name, selector, spec
                    )
                self._append_row(
                    rows,
                    names,
                    self._available_generated_name(f"__qpiai_sos1_card_{constraint.name}", names),
                    {selector: 1.0 for selector in selectors},
                    "<=",
                    1.0,
                    -math.inf,
                    1.0,
                )
            elif isinstance(constraint, SOS2):
                ordered = [
                    variable
                    for _, variable in sorted(
                        zip(constraint.weights, constraint.variables),
                        key=lambda pair: pair[0],
                    )
                ]
                if len(ordered) <= 2:
                    continue
                intervals = []
                for index in range(len(ordered) - 1):
                    interval = self._unique_generated_name(
                        f"__qpiai_sos2_{constraint.name}_{index}", existing_names
                    )
                    intervals.append(interval)
                    new_spec = {
                        "name": interval,
                        "lower": 0.0,
                        "upper": 1.0,
                        "kind": "BINARY",
                        "integrality": 1,
                    }
                    specs.append(new_spec)
                    spec_by_name[interval] = new_spec
                self._append_row(
                    rows,
                    names,
                    self._available_generated_name(
                        f"__qpiai_sos2_interval_{constraint.name}", names
                    ),
                    {interval: 1.0 for interval in intervals},
                    "<=",
                    1.0,
                    -math.inf,
                    1.0,
                )
                for index, variable in enumerate(ordered):
                    spec = spec_by_name[variable.name]
                    self._require_finite_sos_bounds(constraint.name, spec)
                    covering = []
                    if index > 0:
                        covering.append(intervals[index - 1])
                    if index < len(intervals):
                        covering.append(intervals[index])
                    upper_coefficients = {variable.name: 1.0}
                    upper_coefficients.update({interval: -spec["upper"] for interval in covering})
                    lower_coefficients = {variable.name: 1.0}
                    lower_coefficients.update({interval: -spec["lower"] for interval in covering})
                    self._append_row(
                        rows,
                        names,
                        self._available_generated_name(
                            f"__qpiai_sos2_upper_{constraint.name}_{variable.name}",
                            names,
                        ),
                        upper_coefficients,
                        "<=",
                        0.0,
                        -math.inf,
                        0.0,
                    )
                    self._append_row(
                        rows,
                        names,
                        self._available_generated_name(
                            f"__qpiai_sos2_lower_{constraint.name}_{variable.name}",
                            names,
                        ),
                        lower_coefficients,
                        ">=",
                        0.0,
                        0.0,
                        math.inf,
                    )

    def _add_activation_rows(self, rows, names, sos_name, variable_name, selector, spec):
        self._append_row(
            rows,
            names,
            self._available_generated_name(f"__qpiai_sos1_upper_{sos_name}_{variable_name}", names),
            {variable_name: 1.0, selector: -spec["upper"]},
            "<=",
            0.0,
            -math.inf,
            0.0,
        )
        self._append_row(
            rows,
            names,
            self._available_generated_name(f"__qpiai_sos1_lower_{sos_name}_{variable_name}", names),
            {variable_name: 1.0, selector: -spec["lower"]},
            ">=",
            0.0,
            0.0,
            math.inf,
        )

    @staticmethod
    def _require_finite_sos_bounds(sos_name, spec):
        if not math.isfinite(spec["lower"]) or not math.isfinite(spec["upper"]):
            raise InvalidLinearModelError(
                f"SOS '{sos_name}' requires finite member bounds for MILP reformulation"
            )

    @staticmethod
    def _available_generated_name(base, existing):
        candidate = base
        suffix = 1
        while candidate in existing:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    @classmethod
    def _unique_generated_name(cls, base, existing):
        candidate = cls._available_generated_name(base, existing)
        existing.add(candidate)
        return candidate

    def to_lp_string(self, for_milp=True):
        """Return the model in CPLEX LP text format."""

        return "\n".join(self._lp_lines_from_matrix(self.to_lp_matrix(for_milp))) + "\n"

    def to_lp_file(self, filepath, for_milp=True):
        """Write the model to an ``.lp`` or ``.lp.gz`` file."""

        path = Path(filepath)
        lower_name = path.name.lower()
        if not (lower_name.endswith(".lp") or lower_name.endswith(".lp.gz")):
            raise ValueError("LP file path must end in .lp or .lp.gz")
        content = self.to_lp_string(for_milp)
        if lower_name.endswith(".gz"):
            with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
        return str(path)

    def _lp_lines_from_matrix(self, data):
        self._validate_export_names(data)
        lines = [f"\\ Model: {self.name}"]
        lines.append("Minimize" if self.sense == "MIN" else "Maximize")
        objective = self._format_lp_expression(
            data["objective_coefficients"],
            data["variable_names"],
            constant=data["objective_offset"],
        )
        lines.append(f" obj: {objective}")
        lines.append("Subject To")
        if data["constraints"]:
            for constraint in data["constraints"]:
                expression = self._format_lp_expression(
                    constraint["coefficients"], data["variable_names"]
                )
                lines.append(
                    f" {constraint['name']}: {expression} {constraint['sense']} "
                    f"{self._format_number(constraint['rhs'])}"
                )
        else:
            lines.append(" __qpiai_empty_row: 0 <= 0")

        lines.append("Bounds")
        for name, lower, upper in zip(
            data["variable_names"], data["lower_bounds"], data["upper_bounds"]
        ):
            if math.isinf(lower) and math.isinf(upper):
                lines.append(f" {name} free")
            elif lower == upper:
                lines.append(f" {name} = {self._format_number(lower)}")
            elif math.isinf(lower):
                lines.append(
                    f" {self._format_number(lower)} <= {name} <= {self._format_number(upper)}"
                )
            elif math.isinf(upper):
                lines.append(f" {name} >= {self._format_number(lower)}")
            else:
                lines.append(
                    f" {self._format_number(lower)} <= {name} <= {self._format_number(upper)}"
                )
        if not data["variable_names"]:
            lines.append(f" {_CONSTANT_EXPORT_ANCHOR} = 0")

        kinds = data["variable_types"]
        generals = [
            name for name in data["variable_names"] if kinds[name] in {"INTEGER", "SEMI_INTEGER"}
        ]
        binaries = [name for name in data["variable_names"] if kinds[name] == "BINARY"]
        semis = [
            name
            for name in data["variable_names"]
            if kinds[name] in {"SEMI_CONTINUOUS", "SEMI_INTEGER"}
        ]
        if generals:
            lines.append("Generals")
            lines.extend(f" {name}" for name in generals)
        if binaries:
            lines.append("Binaries")
            lines.extend(f" {name}" for name in binaries)
        if semis:
            lines.append("Semi-Continuous")
            lines.extend(f" {name}" for name in semis)
        if data["sos"]:
            lines.append("SOS")
            for sos in data["sos"]:
                members = " ".join(
                    f"{name}:{self._format_number(weight)}"
                    for name, weight in zip(sos["variables"], sos["weights"])
                )
                lines.append(f" {sos['name']}: {sos['type']}:: {members}")
        lines.append("End")
        export_names = [*data["variable_names"], *data["constraint_names"]]
        export_names.extend(sos["name"] for sos in data["sos"])
        if any("[" in name or "]" in name for name in export_names):
            lines = [_LP_BRACKET_HEADER, *(line.translate(_LP_ENCODE_NAMES) for line in lines)]
        return [
            wrapped
            for line in lines
            for wrapped in (
                [line]
                if len(line) <= 999
                else textwrap.wrap(
                    line,
                    width=999,
                    subsequent_indent="  ",
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
        ]

    def to_mps_string(self, for_milp=True):
        """Return the model in free-format MPS text."""

        data = self.to_lp_matrix(for_milp)
        self._validate_export_names(data)
        lines = [f"NAME {self.name}", "OBJSENSE", f" {self.sense}", "ROWS", " N OBJ"]
        row_type = {"=": "E", "<=": "L", ">=": "G"}
        for constraint in data["constraints"]:
            lines.append(f" {row_type[constraint['sense']]} {constraint['name']}")

        lines.append("COLUMNS")
        export_names = data["variable_names"] or [_CONSTANT_EXPORT_ANCHOR]
        for name in export_names:
            entries = []
            objective = data["objective_coefficients"].get(name, 0.0)
            if objective:
                entries.append(("OBJ", objective))
            for constraint in data["constraints"]:
                coefficient = constraint["coefficients"].get(name, 0.0)
                if coefficient:
                    entries.append((constraint["name"], coefficient))
            if not entries:
                entries.append(("OBJ", 0.0))
            for row, value in entries:
                lines.append(f"    {name} {row} {self._format_number(value)}")

        lines.append("RHS")
        wrote_rhs = False
        if data["objective_offset"]:
            lines.append(f"    RHS1 OBJ {self._format_number(-data['objective_offset'])}")
            wrote_rhs = True
        for constraint in data["constraints"]:
            if constraint["rhs"] != 0.0:
                lines.append(
                    f"    RHS1 {constraint['name']} {self._format_number(constraint['rhs'])}"
                )
                wrote_rhs = True
        if not wrote_rhs:
            lines.append("    RHS1 OBJ 0")

        lines.append("BOUNDS")
        for name, lower, upper in zip(
            data["variable_names"], data["lower_bounds"], data["upper_bounds"]
        ):
            kind = data["variable_types"][name]
            self._append_mps_bounds(lines, name, lower, upper, kind)
        if not data["variable_names"]:
            lines.append(f" FX BND1 {_CONSTANT_EXPORT_ANCHOR} 0")
        if data["sos"]:
            lines.append("SOS")
            for sos in data["sos"]:
                lines.append(f" {sos['type']} {sos['name']}")
                for name, weight in zip(sos["variables"], sos["weights"]):
                    lines.append(f"    {name} {self._format_number(weight)}")
        lines.append("ENDATA")
        return "\n".join(lines) + "\n"

    def to_mps_file(self, filepath, for_milp=True):
        """Write the model to an ``.mps`` or ``.mps.gz`` file."""

        path = Path(filepath)
        lower_name = path.name.lower()
        if not (lower_name.endswith(".mps") or lower_name.endswith(".mps.gz")):
            raise ValueError("MPS file path must end in .mps or .mps.gz")
        content = self.to_mps_string(for_milp)
        if lower_name.endswith(".gz"):
            with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
        return str(path)

    def _append_mps_bounds(self, lines, name, lower, upper, kind):
        if kind == "BINARY":
            lines.append(f" BV BND1 {name}")
            return
        if kind == "SEMI_CONTINUOUS":
            lines.append(f" LO BND1 {name} {self._format_number(lower)}")
            lines.append(f" SC BND1 {name} {self._format_number(upper)}")
            return
        if kind == "SEMI_INTEGER":
            lines.append(f" LI BND1 {name} {self._format_number(lower)}")
            lines.append(f" SI BND1 {name} {self._format_number(upper)}")
            return
        if kind == "INTEGER":
            if math.isinf(lower):
                lines.append(f" MI BND1 {name}")
            else:
                lines.append(f" LI BND1 {name} {self._format_number(lower)}")
            if math.isinf(upper):
                lines.append(f" PL BND1 {name}")
            else:
                lines.append(f" UI BND1 {name} {self._format_number(upper)}")
            return
        if lower == upper:
            lines.append(f" FX BND1 {name} {self._format_number(lower)}")
            return
        if math.isinf(lower) and math.isinf(upper):
            lines.append(f" FR BND1 {name}")
            return
        if math.isinf(lower):
            lines.append(f" MI BND1 {name}")
        elif lower != 0.0:
            lines.append(f" LO BND1 {name} {self._format_number(lower)}")
        if math.isinf(upper):
            lines.append(f" PL BND1 {name}")
        else:
            lines.append(f" UP BND1 {name} {self._format_number(upper)}")

    def _validate_export_names(self, data):
        names = list(data["variable_names"]) + list(data["constraint_names"])
        names.extend(sos["name"] for sos in data["sos"])
        for name in names:
            if (
                len(name) > 255
                or not _LP_NAME_PATTERN.fullmatch(name)
                or name.lower() in _LP_RESERVED_NAMES
                or name.lower().startswith("inf")
                or any(character in name for character in "+-*^:")
            ):
                raise InvalidLinearModelError(
                    f"Name '{name}' is not portable to LP/MPS format. Use a letter, '_', '.', "
                    "or '$' first; continue with letters, digits, '_', '.', '$', '[', ']', or "
                    "','; and avoid format keywords and names beginning with 'inf'."
                )

    def _variable_spec(self, variable):
        if variable.var_type == "BINARY":
            return {
                "name": variable.name,
                "lower": 0.0,
                "upper": 1.0,
                "kind": "BINARY",
                "integrality": 1,
            }
        if variable.var_type == "INTEGER":
            kind, integrality = "INTEGER", 1
            lower, upper = variable.lower_bound, variable.upper_bound
        elif variable.var_type == "CONTINUOUS":
            kind, integrality = "CONTINUOUS", 0
            lower, upper = variable.lower_bound, variable.upper_bound
        elif variable.var_type == "SEMI_CONTINUOUS":
            kind, integrality = "SEMI_CONTINUOUS", 2
            lower, upper = variable.lower_bound, variable.upper_bound
        elif variable.var_type == "SEMI_INTEGER":
            kind, integrality = "SEMI_INTEGER", 3
            lower, upper = variable.lower_bound, variable.upper_bound
        elif variable.var_type == "FRACTIONAL":
            kind, integrality = "CONTINUOUS", 0
            lower, upper = variable.lower_bound, variable.upper_bound
        elif variable.var_type == "MIXED":
            values = variable.valid_discrete_values + variable.continuous_range
            kind, integrality = "CONTINUOUS", 0
            lower, upper = min(values), max(values)
        else:
            raise InvalidLinearModelError(f"Unsupported variable type {variable.var_type}")
        valid_numeric_types = (
            isinstance(lower, numbers.Real)
            and not isinstance(lower, bool)
            and isinstance(upper, numbers.Real)
            and not isinstance(upper, bool)
        )
        try:
            lower_float = float(lower)
            upper_float = float(upper)
        except (OverflowError, TypeError, ValueError):
            valid_numeric_types = False
            lower_float = upper_float = math.nan
        if (
            not valid_numeric_types
            or math.isnan(lower_float)
            or math.isnan(upper_float)
            or lower_float > upper_float
            or lower_float == math.inf
            or upper_float == -math.inf
        ):
            raise InvalidLinearModelError(
                f"Variable '{variable.name}' has invalid bounds [{lower}, {upper}]"
            )
        if kind in {"INTEGER", "SEMI_INTEGER"}:
            finite_bounds = [bound for bound in (lower_float, upper_float) if math.isfinite(bound)]
            if any(not bound.is_integer() or abs(bound) > (1 << 53) - 1 for bound in finite_bounds):
                raise InvalidLinearModelError(
                    f"Variable '{variable.name}' has non-integral or inexact integer bounds"
                )
        if kind in {"SEMI_CONTINUOUS", "SEMI_INTEGER"} and (
            not math.isfinite(lower_float) or not math.isfinite(upper_float) or lower_float <= 0
        ):
            raise InvalidLinearModelError(
                f"Variable '{variable.name}' requires finite semi-variable bounds "
                "with 0 < lower <= upper"
            )
        return {
            "name": variable.name,
            "lower": lower_float,
            "upper": upper_float,
            "kind": kind,
            "integrality": integrality,
        }

    def _linear_components(self, expression):
        contributions = []
        for term in expression.terms:
            if len(term.variables) != 1:
                raise InvalidLinearModelError(f"Nonlinear term in linear export: {expression}")
            name = term.variables[0].name
            contributions.append((name, term.coefficient))
        try:
            coefficients = fsum_by_key(contributions)
        except OverflowError as exc:
            raise InvalidLinearModelError("Linear coefficient aggregation overflowed") from exc
        return (
            {name: value for name, value in coefficients.items() if value != 0.0},
            float(expression.constant),
        )

    def _linear_problem_type(self, variables):
        types = {variable.var_type for variable in variables}
        discrete = {"BINARY", "INTEGER", "SEMI_CONTINUOUS", "SEMI_INTEGER", "FRACTIONAL", "MIXED"}
        if (
            any(isinstance(item["constraint"], (SOS1, SOS2)) for item in self.constraints)
            and not types & discrete
        ):
            return "MIP"
        if not types & discrete:
            return "LP"
        return "MILP" if "CONTINUOUS" not in types else "MIP"

    @staticmethod
    def _format_number(value):
        value = float(value)
        if math.isinf(value):
            return "+inf" if value > 0 else "-inf"
        # repr() is the shortest decimal representation that round-trips to the
        # same binary64 value; fixed precision formatting can silently merge
        # distinct accepted bounds or coefficients.
        return str(int(value)) if value.is_integer() else repr(value)

    def _format_lp_expression(self, coefficients, variable_names, constant=0.0):
        parts = []
        for name in variable_names:
            coefficient = float(coefficients.get(name, 0.0))
            if coefficient == 0.0:
                continue
            magnitude = abs(coefficient)
            body = name if magnitude == 1.0 else f"{self._format_number(magnitude)} {name}"
            parts.append(("-" if coefficient < 0 else "+", body))
        if constant != 0.0 or not parts:
            parts.append(("-" if constant < 0 else "+", self._format_number(abs(constant))))
        first_sign, first_body = parts[0]
        result = f"- {first_body}" if first_sign == "-" else first_body
        for sign, body in parts[1:]:
            result += f" {sign} {body}"
        return result

    def _constraint_name(self, index, item):
        return item.get("label") or f"c{index}"

    def objective_as_string(self):
        """Return the source objective and sense as one readable line."""

        if self.objective is None:
            raise ObjectiveError("Model has no objective")
        return f"{'maximize' if self.sense == 'MAX' else 'minimize'}: {self.objective}"

    def summary(self, *, max_constraints=10):
        """Return a concise, deterministic human-readable model summary."""

        if (
            not isinstance(max_constraints, numbers.Integral)
            or isinstance(max_constraints, bool)
            or max_constraints < 0
        ):
            raise ValueError("max_constraints must be a non-negative integer")
        variables = self.variables()
        type_counts = {}
        for variable in variables:
            type_counts[variable.var_type] = type_counts.get(variable.var_type, 0) + 1
        try:
            problem_class = self.validate()
            if problem_class == "MIP":
                problem_class = "MILP"
        except ModelValidationError:
            problem_class = "UNSUPPORTED"
        lines = [
            f"Model: {self.name}",
            f"Class: {problem_class}",
            f"Variables: {len(variables)}"
            + (
                " ("
                + ", ".join(f"{kind}={count}" for kind, count in sorted(type_counts.items()))
                + ")"
                if type_counts
                else ""
            ),
            f"Objective: {self.objective_as_string() if self.objective is not None else 'unset'}",
            f"Constraints: {len(self.constraints)}",
        ]
        for index, item in enumerate(self.constraints[: int(max_constraints)]):
            constraint = item["constraint"]
            name = self._constraint_name(index, item)
            if isinstance(constraint, (Equal, LessEqual, GreaterEqual)):
                body = f"{constraint.lhs} {constraint._symbol()} {constraint.rhs}"
            elif isinstance(constraint, (SOS1, SOS2)):
                body = (
                    f"{constraint.sos_type}("
                    + ", ".join(variable.name for variable in constraint.variables)
                    + ")"
                )
            else:
                body = repr(constraint)
            lines.append(f"  {name}: {body}")
        omitted = len(self.constraints) - int(max_constraints)
        if omitted > 0:
            lines.append(f"  ... {omitted} more")
        return "\n".join(lines)

    def pretty_print(self, *, max_constraints=10):
        """Print and return :meth:`summary`."""

        value = self.summary(max_constraints=max_constraints)
        print(value)
        return value

    def print_objective(self):
        """Print and return :meth:`objective_as_string`."""

        value = self.objective_as_string()
        print(value)
        return value

    @property
    def sense(self):
        """Return ``MIN`` or ``MAX`` for the source objective."""

        return self._sense

    @sense.setter
    def sense(self, value):
        if not isinstance(value, str):
            raise ObjectiveError("Model sense must be MIN or MAX")
        normalized = value.upper()
        if normalized not in {"MIN", "MAX"}:
            raise ObjectiveError("Model sense must be MIN or MAX")
        self._sense = normalized

    @staticmethod
    def _coerce_objective_expression(expression, operation):
        if isinstance(expression, Expression):
            return expression
        if isinstance(expression, Variable):
            return expression._to_expression()
        if is_finite_real(expression):
            return Expression(constant=float(expression))
        raise ObjectiveError(
            f"Cannot {operation} {type(expression).__name__}; pass a variable, expression, "
            "or finite real constant"
        )

    def __repr__(self):
        return f"<Model '{self.name}': {len(self.constraints)} constraints, Sense={self.sense}>"
