"""Explicit polynomial penalties ."""

from ..expressions import Expression
from ..utils.exceptions import ConstraintValidationError
from ..variables import Variable


class CustomPenalty:
    """A user-supplied polynomial contribution with an optional label."""

    def __init__(self, expression, label=None):
        if isinstance(expression, Variable):
            expression = expression._to_expression()
        if not isinstance(expression, Expression):
            raise ConstraintValidationError("CustomPenalty requires an Expression or Variable")
        if label is not None and (
            not isinstance(label, str)
            or not label
            or any(character in label for character in "\x00\r\n")
        ):
            raise ConstraintValidationError(
                "CustomPenalty label must be a non-empty single-line string"
            )
        self.expression = expression
        self.label = label

    def to_penalty(self):
        """Return the supplied polynomial contribution."""

        return self.expression

    def __repr__(self):
        return f"<CustomPenalty: {self.label or 'unlabeled'}>"
