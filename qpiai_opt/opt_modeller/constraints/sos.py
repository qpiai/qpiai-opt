"""Special ordered set constraints and MILP reformulation data."""

from ..._numeric import is_finite_real
from ..utils.exceptions import ConstraintValidationError
from ..variables import Variable
from .relational import Constraint


def _validate_name(name):
    if not isinstance(name, str) or not name:
        raise ConstraintValidationError("SOS name must be a non-empty string")
    if any(character in name for character in "\x00\r\n"):
        raise ConstraintValidationError("SOS name cannot contain NUL or newline characters")
    return name


def _validate_variables(variables):
    try:
        variables = tuple(variables)
    except TypeError as exc:
        raise ConstraintValidationError("SOS variables must be an iterable") from exc
    if not variables:
        raise ConstraintValidationError("SOS set must contain at least one variable")
    if any(not isinstance(variable, Variable) for variable in variables):
        raise ConstraintValidationError("SOS members must be decision variables")
    names = [variable.name for variable in variables]
    if len(names) != len(set(names)):
        raise ConstraintValidationError("SOS set contains duplicate variable names")
    return variables


def _validate_weights(weights, count):
    if weights is None:
        return tuple(float(index) for index in range(1, count + 1))
    try:
        weights = tuple(weights)
    except TypeError as exc:
        raise ConstraintValidationError("SOS weights must be an iterable") from exc
    if len(weights) != count:
        raise ConstraintValidationError(
            f"Number of SOS weights ({len(weights)}) must match members ({count})"
        )
    if any(not is_finite_real(weight) for weight in weights):
        raise ConstraintValidationError("SOS weights must be finite real numbers")
    normalized = tuple(float(weight) for weight in weights)
    if len(normalized) != len(set(normalized)):
        raise ConstraintValidationError("SOS weights must be unique")
    return normalized


class _SOS(Constraint):
    sos_type = None

    def __init__(self, name, variables, weights=None):
        self.name = _validate_name(name)
        self.variables = _validate_variables(variables)
        self.weights = _validate_weights(weights, len(self.variables))

    def _validate_constraint(self):
        return None

    def to_penalty(self):
        """Reject polynomial conversion because SOS requires linear reformulation."""

        raise ConstraintValidationError(
            f"{self.sos_type} constraints require native MILP export or solve"
        )

    def _symbol(self):
        return self.sos_type


class SOS1(_SOS):
    """At most one member may be non-zero."""

    sos_type = "SOS1"

    def __repr__(self):
        return f"<SOS1: {self.name}>"


class SOS2(_SOS):
    """At most two adjacent members in weight order may be non-zero."""

    sos_type = "SOS2"

    def __repr__(self):
        return f"<SOS2: {self.name}>"
