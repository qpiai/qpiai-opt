"""Indicator constraints for conditional linear relations."""

import numbers

from ..utils.exceptions import ConstraintValidationError
from ..variables import Binary
from .relational import Equal, GreaterEqual, LessEqual


class Indicator:
    """Enforce a linear relation when a binary variable has a chosen value."""

    def __init__(self, binary, active_value, constraint):
        if not isinstance(binary, Binary):
            raise ConstraintValidationError("Indicator trigger must be a Binary variable")
        if not isinstance(active_value, (bool, numbers.Integral)) or active_value not in (0, 1):
            raise ConstraintValidationError("Indicator active_value must be 0 or 1")
        if not isinstance(constraint, (Equal, LessEqual, GreaterEqual)):
            raise ConstraintValidationError("Indicator body must be a linear relational constraint")
        self.binary = binary
        self.active_value = int(active_value)
        self.constraint = constraint

    def variables(self):
        """Return the trigger followed by variables in the guarded relation."""

        result = [self.binary]
        seen = {id(self.binary)}
        for expression in (self.constraint.lhs, self.constraint.rhs):
            for term in expression.terms:
                for variable in term.variables:
                    if id(variable) not in seen:
                        result.append(variable)
                        seen.add(id(variable))
        return tuple(result)

    def __bool__(self):
        raise TypeError("A modelling constraint has no truth value; add it to a Model instead")

    def __repr__(self):
        return f"<Indicator: {self.binary.name} == {self.active_value} -> {self.constraint.lhs} {self.constraint._symbol()} {self.constraint.rhs}>"
