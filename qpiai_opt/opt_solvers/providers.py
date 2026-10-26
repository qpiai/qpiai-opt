"""Solver-provider capability records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderCapabilities:
    """Small, stable capability description used by routing and inspection."""

    name: str
    mode: str
    problem_classes: tuple[str, ...]
    exact: bool
    heuristic: bool
    requires_authentication: bool
    diagnostics: tuple[str, ...]
    problem_capabilities: dict[str, dict[str, bool]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe capability mapping."""

        return {
            "name": self.name,
            "mode": self.mode,
            "problem_classes": list(self.problem_classes),
            "exact": self.exact,
            "heuristic": self.heuristic,
            "requires_authentication": self.requires_authentication,
            "diagnostics": list(self.diagnostics),
            "problem_capabilities": {
                problem_class: dict(details)
                for problem_class, details in self.problem_capabilities.items()
            },
        }
