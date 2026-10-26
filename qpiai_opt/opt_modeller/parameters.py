"""Validated mutable parameters bound to supported model data."""

from __future__ import annotations

from dataclasses import dataclass

from .._numeric import is_finite_real


@dataclass
class Parameter:
    """A named finite value that can drive explicit model-update bindings."""

    name: str
    value: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name
            or any(character in self.name for character in "\x00\r\n")
        ):
            raise ValueError("Parameter name must be a non-empty single-line string")
        self.set(self.value)

    def set(self, value: float) -> float:
        """Validate, store, and return a new parameter value."""

        if not is_finite_real(value):
            raise ValueError(f"Parameter '{self.name}' value must be finite")
        self.value = float(value)
        return self.value

    def as_dict(self) -> dict[str, float | str]:
        """Return the parameter as a JSON-compatible mapping."""

        return {"name": self.name, "value": self.value}
