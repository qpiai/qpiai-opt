"""Exceptions raised while constructing, validating, and compiling models."""


class QuboModellerError(Exception):
    """Base class for modeller exceptions."""

    pass


class ModelValidationError(QuboModellerError):
    """Raised when a model does not satisfy the structural rules of its problem class."""

    pass


class InvalidExpressionError(QuboModellerError):
    """Raised when a symbolic expression is invalid or unsupported."""

    pass


class NonlinearExpressionError(InvalidExpressionError):
    """Raised when an expression violates the polynomial degree supported by the target model."""

    pass


class ObjectiveError(QuboModellerError):
    """Raised when the model objective is missing or malformed."""

    pass


class EmptyModelError(ModelValidationError):
    """Raised when a model has no meaningful variables, objective, or compiled matrix."""

    pass


class InvalidQUBOError(ModelValidationError):
    """Raised when a model violates the QUBO definition."""

    pass


class InvalidLinearModelError(ModelValidationError):
    """Raised when a model violates LP/MILP linearity requirements."""

    pass


class HigherOrderTermError(InvalidExpressionError):
    """Raised when a quadratic-only operation receives a term above degree two."""

    pass


class BoundsError(QuboModellerError):
    """Raised when continuous or integer variables have invalid lower/upper bounds."""

    pass


class ModelCompilationError(QuboModellerError):
    """Raised when model compilation or encoder dispatch fails."""

    pass


class EncodingError(QuboModellerError):
    """Raised when variable encoding fails due to invalid parameters or bounds."""

    pass


class NumericalStabilityError(QuboModellerError):
    """Raised when a model cannot be interpreted safely at solver precision."""

    pass


class ConstraintValidationError(QuboModellerError):
    """Raised when constraints are invalid or incompatible."""

    pass
