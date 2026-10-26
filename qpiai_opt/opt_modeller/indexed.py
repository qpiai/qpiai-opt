"""Deterministic indexed collections of decision variables."""

from __future__ import annotations

import itertools
import math
import numbers
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from .expressions import quicksum
from .variables import Variable


def _index_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        try:
            normalized = float(value)
        except (OverflowError, TypeError, ValueError):
            normalized = math.nan
        if not math.isfinite(normalized):
            raise ValueError(f"Index {value!r} must be finite")
        return str(normalized)
    if isinstance(value, str):
        if value and not any(character in value for character in "\x00\r\n,[]"):
            return value
    raise ValueError(
        f"Index {value!r} cannot form an unambiguous variable name; use a simple string or number"
    )


class IndexedVariables(Mapping):
    """Deterministic one- or multidimensional variables keyed by index tuples."""

    def __init__(
        self,
        variable_type: type[Variable],
        name: str,
        *index_sets: Iterable[Any],
        **variable_options: Any,
    ) -> None:
        if (
            not isinstance(name, str)
            or not name
            or any(character in name for character in "\x00\r\n,[]")
        ):
            raise ValueError(
                "Indexed variable name must be a non-empty string without line breaks, "
                "commas, or brackets"
            )
        if not isinstance(variable_type, type) or not issubclass(variable_type, Variable):
            raise TypeError("variable_type must be a Variable class")
        if not index_sets:
            raise ValueError("At least one index set is required")
        axes = []
        for position, axis in enumerate(index_sets):
            try:
                normalized_axis = tuple(axis)
            except TypeError as exc:
                raise ValueError(f"Index set {position} must be an iterable") from exc
            axes.append(normalized_axis)
        for axis_position, axis in enumerate(axes):
            for value_position, value in enumerate(axis):
                try:
                    _index_text(value)
                except ValueError as exc:
                    raise ValueError(
                        f"Index set {axis_position} item {value_position} is invalid: {exc}"
                    ) from exc
            if len(axis) != len(set(axis)):
                raise ValueError(f"Index set {axis_position} must contain unique values")
        self.name = name
        self.shape = tuple(len(axis) for axis in axes)
        self.index_sets = tuple(axes)
        self._dimension = len(axes)
        self._variables = {}
        used_names = set()
        for key in itertools.product(*axes):
            variable_name = f"{name}[{','.join(_index_text(value) for value in key)}]"
            if variable_name in used_names:
                raise ValueError("Distinct indices produced duplicate variable names")
            used_names.add(variable_name)
            self._variables[key] = variable_type(variable_name, **variable_options)

    def _key(self, key: Any) -> tuple[Any, ...]:
        if self._dimension == 1 and not isinstance(key, tuple):
            normalized = (key,)
        elif not isinstance(key, tuple) or len(key) != self._dimension:
            raise KeyError(f"Expected a {self._dimension}-dimensional index")
        else:
            normalized = key
        try:
            for value in normalized:
                hash(value)
        except TypeError as exc:
            raise KeyError("Indexed-variable lookup values must be hashable") from exc
        return normalized

    def __getitem__(self, key: Any) -> Variable:
        return self._variables[self._key(key)]

    def __iter__(self) -> Iterator[Any]:
        for key in self._variables:
            yield key[0] if self._dimension == 1 else key

    def __len__(self) -> int:
        return len(self._variables)

    def values(self):
        """Return variables in deterministic index-product order."""

        return self._variables.values()

    def items(self):
        """Yield public indices paired with their variables."""

        for key, variable in self._variables.items():
            yield (key[0] if self._dimension == 1 else key), variable

    def select(self, *pattern: Any) -> tuple[Variable, ...]:
        """Select variables; ``None`` or ``'*'`` is a wildcard for one axis."""

        if len(pattern) != self._dimension:
            raise ValueError(f"select expects {self._dimension} selectors")

        def matches(selector, value):
            if selector is None or (isinstance(selector, str) and selector == "*"):
                return True
            try:
                return bool(selector == value)
            except (TypeError, ValueError):
                return False

        return tuple(
            variable
            for key, variable in self._variables.items()
            if all(matches(selector, value) for selector, value in zip(pattern, key))
        )

    def sum(self, *pattern: Any):
        """Return a symbolic sum over all values or a selected slice."""

        variables = self.values() if not pattern else self.select(*pattern)
        return quicksum(variables)
