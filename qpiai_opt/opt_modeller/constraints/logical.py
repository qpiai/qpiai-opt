"""Binary logical constraint constructors and exact penalties."""

from ..utils.exceptions import ConstraintValidationError
from ..variables import Binary, auxiliary_name


def _validate_binary_variables(constraint_name, *variables):
    if any(not isinstance(variable, Binary) for variable in variables):
        raise ConstraintValidationError(f"{constraint_name} accepts Binary variables only")
    if len({id(variable) for variable in variables}) != len(variables):
        raise ConstraintValidationError(
            f"{constraint_name} requires distinct input and output variables"
        )


class LogicalConstraint:
    """Base interface for binary logical constraints."""

    def to_penalty(self):
        """Return an exact non-negative polynomial penalty."""

        raise NotImplementedError

    def variables(self):
        """Return participating source and auxiliary variables."""

        raise NotImplementedError

    def __bool__(self):
        raise TypeError("A modelling constraint has no truth value; add it to a Model instead")


class AND(LogicalConstraint):
    """Enforce ``z = x AND y`` with an exact quadratic penalty."""

    def __init__(self, x, y, z):
        _validate_binary_variables("AND", x, y, z)
        self.x, self.y, self.z = x, y, z

    def variables(self):
        """Return ``x``, ``y``, and ``z``."""

        return self.x, self.y, self.z

    def to_penalty(self):
        """Return the exact AND penalty."""

        return self.x * self.y - 2 * self.x * self.z - 2 * self.y * self.z + 3 * self.z

    def __repr__(self):
        return f"<Logical: {self.z.name} == {self.x.name} AND {self.y.name}>"


class OR(LogicalConstraint):
    """Enforce ``z = x OR y`` with an exact quadratic penalty."""

    def __init__(self, x, y, z):
        _validate_binary_variables("OR", x, y, z)
        self.x, self.y, self.z = x, y, z

    def variables(self):
        """Return ``x``, ``y``, and ``z``."""

        return self.x, self.y, self.z

    def to_penalty(self):
        """Return the exact OR penalty."""

        return (
            self.x * self.y - 2 * self.x * self.z - 2 * self.y * self.z + self.x + self.y + self.z
        )

    def __repr__(self):
        return f"<Logical: {self.z.name} == {self.x.name} OR {self.y.name}>"


class XOR(LogicalConstraint):
    """Enforce ``z = x XOR y`` using one exact binary auxiliary.

    XOR has no non-negative quadratic penalty over only ``x, y, z``. The
    identity ``x + y - z - 2a = 0`` is exact when ``a`` is binary.
    """

    def __init__(self, x, y, z):
        _validate_binary_variables("XOR", x, y, z)
        self.x, self.y, self.z = x, y, z
        self.auxiliary = Binary(auxiliary_name("xor", x.name, y.name, z.name))
        self.auxiliary._is_auxiliary = True

    def variables(self):
        """Return source variables followed by the XOR auxiliary."""

        return self.x, self.y, self.z, self.auxiliary

    def to_penalty(self):
        """Return the exact XOR penalty."""

        return (self.x + self.y - self.z - 2 * self.auxiliary) ** 2

    def __repr__(self):
        return f"<Logical: {self.z.name} == {self.x.name} XOR {self.y.name}>"


class NOT(LogicalConstraint):
    """Enforce ``y = NOT x``."""

    def __init__(self, x, y):
        _validate_binary_variables("NOT", x, y)
        self.x, self.y = x, y

    def variables(self):
        """Return ``x`` and ``y``."""

        return self.x, self.y

    def to_penalty(self):
        """Return the exact NOT penalty."""

        return (self.x + self.y - 1) ** 2

    def __repr__(self):
        return f"<Logical: {self.y.name} == NOT {self.x.name}>"
