"""Validated Ising problems in source objective coordinates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from ...opt_modeller.converters import _IsingData


class IsingProblem(BaseModel):
    """Indexed spin objective, with source coefficients and optimization sense.

    Energy is offset + sum(h[i]*s[i]) + sum(J[i,j]*s[i]*s[j]).
    Each spin is -1 or +1. Maximization preserves these source coefficients;
    adapters normalize the energy only when an algorithm requires minimization.
    """

    problem_type: Literal["ISING"] = "ISING"
    num_variables: int = Field(strict=True, ge=0)
    sense: Literal["MIN", "MAX"] = "MIN"
    offset: float = 0.0
    variable_names: tuple[str, ...] = ()
    linear: dict[int, float]
    quadratic: dict[tuple[int, int], float]
    _source_model: Any = PrivateAttr(default=None)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def validate_coefficients(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "num_variables" not in data or "linear" not in data or "quadratic" not in data:
            return data
        validated = _IsingData(
            linear=data["linear"],
            quadratic=data["quadratic"],
            num_variables=data["num_variables"],
            offset=data.get("offset", 0.0),
            variable_names=data.get("variable_names", ()),
            original_sense=data.get("sense", "MIN"),
        )
        marker = data.get("problem_type", "ISING")
        if not isinstance(marker, str) or marker.upper() != "ISING":
            raise ValueError("problem_type must be 'ISING'")
        data.update(
            problem_type="ISING",
            linear=validated.linear,
            quadratic=validated.quadratic,
            offset=validated.offset,
            variable_names=validated.variable_names,
            sense=validated.original_sense,
        )
        return data

    @classmethod
    def from_model(cls, model, **compile_options):
        """Compile and retain an independent source and decoding snapshot."""

        from ...opt_modeller.model import Model

        if not isinstance(model, Model):
            raise TypeError("from_model expects a qpiai_opt.opt_modeller.Model")
        with model._compilation_transaction():
            problem = cls(**model.to_ising_dict(**compile_options))
            problem._source_model = deepcopy(model)
            return problem

    def _as_minimization(self) -> _IsingData:
        sign = -1.0 if self.sense == "MAX" else 1.0
        return _IsingData(
            linear={index: sign * value for index, value in self.linear.items()},
            quadratic={key: sign * value for key, value in self.quadratic.items()},
            offset=sign * self.offset,
            num_variables=self.num_variables,
            variable_names=self.variable_names,
            original_sense=self.sense,
        )

    @classmethod
    def _from_minimization(cls, converted):
        sign = -1.0 if converted.original_sense == "MAX" else 1.0
        return cls(
            linear={index: sign * value for index, value in converted.linear.items()},
            quadratic={key: sign * value for key, value in converted.quadratic.items()},
            offset=sign * converted.offset,
            num_variables=converted.num_variables,
            variable_names=converted.variable_names,
            sense=converted.original_sense,
        )

    def energy(self, sample):
        """Evaluate the source spin objective, including its offset."""

        from ...opt_modeller.converters import _ising_energy

        return _ising_energy(
            self.linear,
            self.quadratic,
            sample,
            self.offset,
            self.num_variables,
            variable_names=self.variable_names,
        )

    def decode(self, sample):
        """Return source values, restoring binary encodings when compiled."""

        from ...opt_modeller.converters import _validated_indexed_sample

        spins = _validated_indexed_sample(
            sample,
            self.num_variables,
            (-1, 1),
            "Ising",
            variable_names=self.variable_names,
        )
        if self._source_model is not None:
            source_variables = self._source_model.variables()
            if not all(variable.var_type == "SPIN" for variable in source_variables):
                return self._source_model.decode_qubo_sample([(value + 1) // 2 for value in spins])
        names = self.variable_names or tuple(f"s[{index}]" for index in range(self.num_variables))
        return dict(zip(names, spins))

    def source_objective(self, sample):
        """Evaluate the original model when source decoding is available."""

        if self._source_model is not None:
            return float(self._source_model.evaluate_objective(self.decode(sample)))
        return self.energy(sample)
