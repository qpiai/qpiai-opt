"""Solver routing, platform clients, result models, and automation helpers."""

from . import hubo, ising, milp, qubo
from .automation import cancel, convert, feasibility, result, schemas, status, submit
from .client import Client
from .exceptions import (
    APIError,
    AuthenticationError,
    CapabilityError,
    JobCancelledError,
    JobFailedError,
    LocalLimitExceeded,
    PlatformError,
)
from .local import LOCAL_LIMITS, LocalProvider
from .platform_models import (
    ComputeResource,
    CreditQuota,
    CreditResourceUsage,
    CreditUsage,
    CreditUsageByResource,
    CreditUsageJob,
    Experiment,
    Invitation,
    Job,
    JobStatus,
    Organization,
    Problem,
    Project,
    SSEEvent,
    UserSummary,
    Workspace,
)
from .result_base import SolveInfo
from .results import IsingSolveResult, QPSolveResult, SolveResult
from .routing import classify, inspect, provider_capabilities, solve

__all__ = [
    "milp",
    "qubo",
    "hubo",
    "ising",
    "Client",
    "Problem",
    "Job",
    "JobStatus",
    "ComputeResource",
    "CreditQuota",
    "CreditResourceUsage",
    "CreditUsage",
    "CreditUsageByResource",
    "CreditUsageJob",
    "Organization",
    "Workspace",
    "Project",
    "Experiment",
    "Invitation",
    "UserSummary",
    "SSEEvent",
    "PlatformError",
    "APIError",
    "AuthenticationError",
    "CapabilityError",
    "JobFailedError",
    "JobCancelledError",
    "LocalLimitExceeded",
    "SolveResult",
    "SolveInfo",
    "IsingSolveResult",
    "QPSolveResult",
    "LocalProvider",
    "LOCAL_LIMITS",
    "classify",
    "inspect",
    "provider_capabilities",
    "solve",
    "convert",
    "feasibility",
    "submit",
    "status",
    "result",
    "cancel",
    "schemas",
]
