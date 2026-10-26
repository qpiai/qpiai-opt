"""Lower validated model expressions into compiler coefficient maps."""

from collections import OrderedDict

import numpy as np

from ..._numeric import finite_fsum, fsum_by_key
from ..expressions import Expression
from ..utils.exceptions import HigherOrderTermError, InvalidQUBOError
from ..variables import Variable


def lower_expression(expression):
    """Lower a binary quadratic expression to compact upper-triangle triplets.

    Column numbers are local to this expression and follow first use. Repeated
    factors of the same binary variable are reduced using ``x**n == x`` and
    duplicate matrix entries are summed.
    """

    if isinstance(expression, Variable):
        expression = expression._to_expression()
    if not isinstance(expression, Expression):
        raise TypeError("lower_expression expects an Expression or Variable")

    variable_columns = OrderedDict()
    variable_names = {}
    contributions = []
    constant_contributions = [float(expression.constant)]

    for term in expression.terms:
        if term.coefficient == 0.0:
            continue
        factors = []
        seen = set()
        for variable in term.variables:
            if variable.var_type != "BINARY":
                raise InvalidQUBOError(
                    f"Variable '{variable.name}' must be binary before QUBO lowering"
                )
            existing = variable_names.get(variable.name)
            if existing is not None and existing is not variable:
                raise InvalidQUBOError(
                    f"Expression contains distinct variables named '{variable.name}'"
                )
            variable_names[variable.name] = variable
            if variable not in variable_columns:
                variable_columns[variable] = len(variable_columns)
            if variable not in seen:
                factors.append(variable)
                seen.add(variable)

        if not factors:
            constant_contributions.append(float(term.coefficient))
        elif len(factors) == 1:
            column = variable_columns[factors[0]]
            key = (column, column)
            contributions.append((key, float(term.coefficient)))
        elif len(factors) == 2:
            left, right = sorted(variable_columns[variable] for variable in factors)
            key = (left, right)
            contributions.append((key, float(term.coefficient)))
        else:
            raise HigherOrderTermError(
                f"Term has binary degree {len(factors)}; QUBO supports degree at most two"
            )

    try:
        coefficients = fsum_by_key(contributions)
        constant = finite_fsum(constant_contributions)
    except OverflowError as exc:
        raise InvalidQUBOError("QUBO coefficient aggregation overflowed") from exc
    active = sorted((key, value) for key, value in coefficients.items() if value != 0.0)
    rows = np.fromiter((key[0] for key, _ in active), dtype=np.int64, count=len(active))
    cols = np.fromiter((key[1] for key, _ in active), dtype=np.int64, count=len(active))
    values = np.fromiter((value for _, value in active), dtype=np.float64, count=len(active))
    return rows, cols, values, constant
