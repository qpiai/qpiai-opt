"""Optional bidirectional bridge for the supported OMMX model subset."""

from __future__ import annotations

from .._numeric import finite_fsum, fsum_by_key, is_finite_real
from .constraints import Equal, GreaterEqual, LessEqual
from .expressions import Expression, Term
from .model import Model
from .utils.exceptions import ModelValidationError
from .variables import Binary, Continuous, Integer, SemiContinuous, SemiInteger


def _ommx_api():
    try:
        from ommx.v1 import Constraint, DecisionVariable, Instance, Polynomial
    except ImportError as exc:
        raise ImportError(
            "OMMX interoperability requires the optional dependency; install qpiai-opt[ommx]"
        ) from exc
    return Constraint, DecisionVariable, Instance, Polynomial


def to_ommx(model: Model):
    """Convert supported QpiAI model components to an OMMX instance."""

    if not isinstance(model, Model):
        raise TypeError("to_ommx expects a Model")
    constraint_type, variable_type, instance_type, polynomial_type = _ommx_api()
    variables = model._require_model()
    ids = {id(variable): index for index, variable in enumerate(variables)}
    constructors = {
        "BINARY": lambda index, variable: variable_type.binary(index, name=variable.name),
        "INTEGER": lambda index, variable: variable_type.integer(
            index,
            lower=variable.lower_bound,
            upper=variable.upper_bound,
            name=variable.name,
        ),
        "CONTINUOUS": lambda index, variable: variable_type.continuous(
            index,
            lower=variable.lower_bound,
            upper=variable.upper_bound,
            name=variable.name,
        ),
        "SEMI_CONTINUOUS": lambda index, variable: variable_type.semi_continuous(
            index,
            lower=variable.lower_bound,
            upper=variable.upper_bound,
            name=variable.name,
        ),
        "SEMI_INTEGER": lambda index, variable: variable_type.semi_integer(
            index,
            lower=variable.lower_bound,
            upper=variable.upper_bound,
            name=variable.name,
        ),
    }
    unsupported = [variable.name for variable in variables if variable.var_type not in constructors]
    if unsupported:
        raise ModelValidationError(
            "OMMX conversion does not support these QpiAI variable domains: "
            + ", ".join(unsupported)
        )

    def polynomial(expression):
        contributions = [
            (
                tuple(sorted(ids[id(variable)] for variable in term.variables)),
                float(term.coefficient),
            )
            for term in expression.terms
        ]
        if expression.constant:
            contributions.append(((), float(expression.constant)))
        try:
            terms = fsum_by_key(contributions)
        except OverflowError as exc:
            raise ModelValidationError(
                "OMMX polynomial coefficient aggregation overflowed"
            ) from exc
        return polynomial_type(terms={key: value for key, value in terms.items() if value != 0.0})

    constraints = []
    for index, item in enumerate(model.constraints):
        relation = item["constraint"]
        if not isinstance(relation, (Equal, LessEqual, GreaterEqual)):
            raise ModelValidationError(
                f"OMMX conversion cannot preserve {type(relation).__name__} constraint "
                f"{model._constraint_name(index, item)}"
            )
        difference = relation.lhs - relation.rhs
        if isinstance(relation, GreaterEqual):
            difference = -difference
        equality = (
            constraint_type.EQUAL_TO_ZERO
            if isinstance(relation, Equal)
            else constraint_type.LESS_THAN_OR_EQUAL_TO_ZERO
        )
        constraints.append(
            constraint_type(
                function=polynomial(difference),
                equality=equality,
                id=index,
                name=model._constraint_name(index, item),
            )
        )
    instance = instance_type.from_components(
        objective=polynomial(model.objective),
        constraints=constraints,
        sense=instance_type.MAXIMIZE if model.sense == "MAX" else instance_type.MINIMIZE,
        decision_variables=[
            constructors[variable.var_type](index, variable)
            for index, variable in enumerate(variables)
        ],
    )
    model.transformation_metadata.append(
        {
            "stage": "interoperability",
            "target": "OMMX",
            "variable_ids": {variable.name: index for index, variable in enumerate(variables)},
            "preserved": ["objective", "sense", "domains", "bounds", "constraints"],
        }
    )
    return instance


def from_ommx(instance) -> Model:
    """Convert a supported OMMX instance to a QpiAI model."""

    constraint_type, variable_type, instance_type, _ = _ommx_api()
    if not isinstance(instance, instance_type):
        raise TypeError("from_ommx expects ommx.v1.Instance")
    model = Model(instance.title or "OMMX model")
    variables = {}

    def convert_variable(variable, name):
        if variable.kind == variable_type.BINARY:
            return Binary(name)
        if variable.kind == variable_type.INTEGER:
            return Integer(name, variable.lower, variable.upper)
        if variable.kind == variable_type.CONTINUOUS:
            return Continuous(name, variable.lower, variable.upper)
        if variable.kind == variable_type.SEMI_CONTINUOUS:
            return SemiContinuous(name, variable.lower, variable.upper)
        if variable.kind == variable_type.SEMI_INTEGER:
            return SemiInteger(name, variable.lower, variable.upper)
        raise ModelValidationError(f"Unsupported OMMX variable kind {variable.kind}")

    names = set()
    for variable in instance.decision_variables:
        if variable.id in variables:
            raise ModelValidationError(f"OMMX variable id {variable.id} is not unique")
        name = variable.name or f"x[{variable.id}]"
        if name in names:
            raise ModelValidationError(f"OMMX variable name {name!r} is not unique")
        names.add(name)
        converted = convert_variable(variable, name)
        variables[variable.id] = converted
        model.add_variable(converted)

    def expression(function, description):
        if function.degree() > 2 and description != "objective":
            raise ModelValidationError(
                f"{description} has degree {function.degree()}; QpiAI relational constraints "
                "must be linear"
            )
        terms = []
        constant_contributions = []
        for ids, coefficient in function.terms.items():
            if not is_finite_real(coefficient):
                raise ModelValidationError(f"{description} contains a non-finite coefficient")
            if not ids:
                constant_contributions.append(float(coefficient))
                continue
            try:
                factors = [variables[variable_id] for variable_id in ids]
            except KeyError as exc:
                raise ModelValidationError(
                    f"{description} references unknown OMMX variable id {exc.args[0]}"
                ) from exc
            terms.append(Term(float(coefficient), factors))
        try:
            constant = finite_fsum(constant_contributions)
        except OverflowError as exc:
            raise ModelValidationError(f"{description} constant aggregation overflowed") from exc
        return Expression(terms=terms, constant=constant)

    objective = expression(instance.objective, "objective")
    if instance.sense == instance_type.MINIMIZE:
        model.minimize(objective)
    elif instance.sense == instance_type.MAXIMIZE:
        model.maximize(objective)
    else:
        raise ModelValidationError(f"Unsupported OMMX objective sense {instance.sense}")
    for constraint in instance.constraints:
        function = expression(constraint.function, f"constraint {constraint.id}")
        if function.degree(raise_on_higher_order=False) > 1:
            raise ModelValidationError(
                f"OMMX constraint {constraint.id} is nonlinear; quadratic constraints are "
                "not supported"
            )
        if constraint.equality == constraint_type.EQUAL_TO_ZERO:
            relation = function == 0.0
        elif constraint.equality == constraint_type.LESS_THAN_OR_EQUAL_TO_ZERO:
            relation = function <= 0.0
        else:
            raise ModelValidationError(
                f"Unsupported OMMX constraint relation {constraint.equality}"
            )
        model.add_constraint(relation, label=constraint.name or f"c{constraint.id}")
    model.transformation_metadata.append(
        {
            "stage": "interoperability",
            "source": "OMMX",
            "variable_ids": {
                variable.name: identifier for identifier, variable in variables.items()
            },
            "preserved": ["objective", "sense", "domains", "bounds", "constraints"],
        }
    )
    return model
