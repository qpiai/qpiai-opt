"""Shared numeric predicates for public validation boundaries."""

from __future__ import annotations

import math
import numbers
from collections.abc import Collection, Iterable
from fractions import Fraction
from typing import Hashable, TypeVar

# Match local MIP feasibility so public verification accepts valid incumbents.
DEFAULT_FEASIBILITY_TOLERANCE = 1e-6

# Minimum allowance for the rounded absolute platform objective difference.
OBJECTIVE_CONSISTENCY_TOLERANCE = 1
OBJECTIVE_CONSISTENCY_RELATIVE_TOLERANCE = 1e-7


# Binary64 represents every integer exactly through 2**53. Larger integers
# may round when passed to the local solver's binary64 numeric interface.
BINARY64_EXACT_INTEGER_LIMIT = 2**53

# Reject matrix values that the local solver would drop or reject on loading.
LOCAL_SMALL_MATRIX_VALUE = 1e-9
LOCAL_LARGE_MATRIX_VALUE = 1e15

# The local solver treats costs and bounds at this magnitude as infinity.
# The SDK rejects such finite values before they can change model semantics.
LOCAL_FINITE_VALUE_LIMIT = 1e20

_Key = TypeVar("_Key", bound=Hashable)


def _objective_values_agree(reported: float, recomputed: float) -> bool:
    difference = abs(reported - recomputed)
    tolerance = max(
        OBJECTIVE_CONSISTENCY_TOLERANCE,
        OBJECTIVE_CONSISTENCY_RELATIVE_TOLERANCE * max(1.0, abs(reported), abs(recomputed)),
    )
    return math.isfinite(difference) and round(difference) <= tolerance


def finite_fsum(values: Collection[float]) -> float:
    """Return an accurate finite binary64 sum or raise ``OverflowError``."""

    try:
        total = math.fsum(values)
    except (OverflowError, ValueError):
        try:
            exact = sum(
                (Fraction.from_float(float(value)) for value in values),
                start=Fraction(),
            )
            total = float(exact)
        except (OverflowError, TypeError, ValueError) as fallback_exc:
            raise OverflowError("floating-point sum is not finite") from fallback_exc
    if not math.isfinite(total):
        raise OverflowError("floating-point sum is not finite")
    return total


def fsum_by_key(items: Iterable[tuple[_Key, float]]) -> dict[_Key, float]:
    """Accurately aggregate numeric contributions by hashable key."""

    groups: dict[_Key, float | list[float]] = {}
    for key, value in items:
        normalized = float(value)
        if not math.isfinite(normalized):
            raise OverflowError("floating-point contribution is not finite")
        previous = groups.get(key)
        if previous is None:
            groups[key] = normalized
        elif isinstance(previous, list):
            previous.append(normalized)
        else:
            groups[key] = [previous, normalized]
    return {
        key: finite_fsum(values) if isinstance(values, list) else values
        for key, values in groups.items()
    }


def is_finite_real(value, *, allow_bool: bool = False) -> bool:
    """Return whether a value is a finite real representable as binary64."""

    if (isinstance(value, bool) and not allow_bool) or not isinstance(value, numbers.Real):
        return False
    try:
        normalized = float(value)
        try:
            underflowed = normalized == 0.0 and value != 0
        except (TypeError, ValueError):
            return False
        return math.isfinite(normalized) and not underflowed
    except (OverflowError, TypeError, ValueError):
        return False


def is_real_or_infinity(value, *, allow_bool: bool = False) -> bool:
    """Return whether a value is a real binary64 number other than NaN."""

    if (isinstance(value, bool) and not allow_bool) or not isinstance(value, numbers.Real):
        return False
    try:
        normalized = float(value)
        try:
            underflowed = normalized == 0.0 and value != 0
        except (TypeError, ValueError):
            return False
        return not math.isnan(normalized) and not underflowed
    except (OverflowError, TypeError, ValueError):
        return False
