"""Structured SDK, routing, platform, and job exceptions."""

from __future__ import annotations

from typing import Any, Mapping, Optional


def _add_exception_note(error: BaseException, note: str) -> None:
    """Attach recovery context on every supported Python version."""

    add_note = getattr(error, "add_note", None)
    if add_note is not None:
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", ()))
    notes.append(note)
    error.__notes__ = notes
    if error.args:
        error.args = (f"{error.args[0]}\nNote: {note}", *error.args[1:])
    else:
        error.args = (f"Note: {note}",)


class PlatformError(Exception):
    """Base exception for SDK solver and platform-client errors."""


class APIError(PlatformError):
    """Platform HTTP error with optional status, code, and response details."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details


class AuthenticationError(APIError):
    """Local or platform credential verification or authorization failure."""

    pass


class JobFailedError(PlatformError):
    """Terminal platform job failure."""

    def __init__(self, job_id: str, message: str) -> None:
        super().__init__(f"Job {job_id} failed: {message}")
        self.job_id = job_id


class JobCancelledError(PlatformError):
    """Terminal platform job cancellation."""

    def __init__(self, job_id: str, message: str = "cancelled") -> None:
        super().__init__(f"Job {job_id} was cancelled: {message}")
        self.job_id = job_id


class CapabilityError(PlatformError):
    """Raised when no available provider can solve the detected problem class."""

    def __init__(
        self,
        problem_class: str,
        *,
        requested_mode: str,
        available_providers: Mapping[str, tuple[str, ...]],
        reason: str | None = None,
        compatible_alternatives: tuple[str, ...] = (),
    ) -> None:
        self.problem_class = problem_class
        self.requested_mode = requested_mode
        self.available_providers = dict(available_providers)
        self.reason = reason or f"no {requested_mode} provider supports {problem_class}"
        self.compatible_alternatives = tuple(compatible_alternatives)
        message = (
            f"Cannot use {requested_mode} for {problem_class}: {self.reason}. "
            f"Available capabilities: {self.available_providers}."
        )
        if self.compatible_alternatives:
            message += f" Alternatives: {', '.join(self.compatible_alternatives)}."
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        """Return structured routing-failure details."""

        return {
            "error": "capability_error",
            "problem_class": self.problem_class,
            "requested_mode": self.requested_mode,
            "reason": self.reason,
            "available_providers": self.available_providers,
            "compatible_alternatives": list(self.compatible_alternatives),
        }


class LocalLimitExceeded(PlatformError):
    """A structured refusal when a problem exceeds a documented local limit."""

    def __init__(
        self,
        problem_class: str,
        *,
        observed: Mapping[str, int | float | str],
        limits: Mapping[str, int | float],
        explanation: str,
        next_action: str,
    ) -> None:
        self.problem_class = problem_class
        self.observed = dict(observed)
        self.limits = dict(limits)
        self.explanation = explanation
        self.next_action = next_action
        super().__init__(
            f"Local {problem_class} limit exceeded: {explanation}. Next action: {next_action}"
        )

    def as_dict(self) -> dict[str, Any]:
        """Return observed limits and the available next action."""

        return {
            "error": "local_limit_exceeded",
            "problem_class": self.problem_class,
            "observed": self.observed,
            "limits": self.limits,
            "explanation": self.explanation,
            "next_action": self.next_action,
        }
