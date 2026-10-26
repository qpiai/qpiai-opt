"""Validated records returned by QpiAI Platform endpoints."""

from __future__ import annotations

import numbers
from datetime import datetime
from enum import Enum
from typing import Any, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from .._numeric import is_finite_real


class PlatformModel(BaseModel):
    """Base for platform records that preserve additional backend fields."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _normalize_id_aliases(cls, value):
        if not isinstance(value, dict):
            return value
        field = cls.model_fields.get("id")
        if field is None:
            return value
        alias = field.validation_alias
        if alias == "ID":
            canonical, alternate = "ID", "id"
        elif isinstance(alias, AliasChoices) and alias.choices == ["id", "ID"]:
            canonical, alternate = "id", "ID"
        else:
            return value
        if alternate not in value:
            return value
        data = dict(value)
        alternate_id = data.pop(alternate)
        if canonical in data and (
            cls.non_empty_string(data[canonical], canonical)
            != cls.non_empty_string(alternate_id, alternate)
        ):
            raise ValueError("id and ID must identify the same platform record")
        data.setdefault(canonical, alternate_id)
        return data

    @model_validator(mode="before")
    @classmethod
    def normalize_backend_timing(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        for wire_name, public_name in (
            ("execution_time", "compute_time_s"),
            ("solver_time", "solver_time_s"),
            ("total_execution_time", "total_compute_time_s"),
        ):
            if public_name not in cls.model_fields or wire_name not in data:
                continue
            duration = data.pop(wire_name)
            if public_name in data and data[public_name] != duration:
                raise ValueError(f"{wire_name} and {public_name} must describe the same duration")
            data[public_name] = duration
        return data

    @staticmethod
    def non_empty_string(value, description):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{description} must be a non-empty string")
        return value.strip()

    @classmethod
    def optional_non_empty_string(cls, value, description):
        """Normalize an absent backend string while rejecting malformed text."""

        if value is None or (isinstance(value, str) and value == ""):
            return None
        return cls.non_empty_string(value, description)

    @staticmethod
    def finite_non_negative(value, description):
        if not is_finite_real(value) or value < 0:
            raise ValueError(f"{description} must be a finite non-negative number")
        return float(value)


class JobStatus(str, Enum):
    """Normalized lifecycle states used by SDK job operations."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @classmethod
    def normalize(cls, value: Any) -> "JobStatus":
        status = str(value or "").strip().lower().replace("_", "")
        if status in {"pending", "queued", "scheduled"}:
            return cls.PENDING
        if status in {"running", "cancelrequested", "inprogress"}:
            return cls.RUNNING
        if status in {"success", "completed", "complete", "succeeded"}:
            return cls.COMPLETED
        if status in {"failed", "failure", "error"}:
            return cls.FAILED
        if status in {"cancelled", "canceled"}:
            return cls.CANCELLED
        return cls.UNKNOWN


class Problem(PlatformModel):
    """Platform problem record."""

    id: str
    name: str
    method: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("id", "name", "method", mode="before")
    @classmethod
    def validate_required_text(cls, value, info):
        return cls.non_empty_string(value, info.field_name)


class Pagination(PlatformModel):
    """Pagination metadata for platform list responses."""

    current_page: int = Field(default=1, strict=True, ge=1)
    page_size: int = Field(default=10, strict=True, ge=1)
    total: int = Field(default=0, strict=True, ge=0)


class ProblemList(PlatformModel):
    """Paginated platform problem records."""

    problems: List[Problem] = Field(default_factory=list)
    pagination: Pagination = Field(default_factory=Pagination)


class Job(PlatformModel):
    """Platform job record and lifecycle metadata."""

    id: str
    status: str
    method: Optional[str] = None
    problem_id: Optional[str] = None
    experiment_id: Optional[str] = None
    server_response: Any = None
    server_response_id: Optional[str] = None
    error: Optional[str] = None
    compute_time_s: Optional[float] = None
    solver_time_s: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None
    credits_used: Optional[int] = None
    credits_reserved: Optional[int] = None

    @field_validator("id", "status", mode="before")
    @classmethod
    def validate_required_text(cls, value, info):
        return cls.non_empty_string(value, info.field_name)

    @field_validator(
        "method",
        "problem_id",
        "experiment_id",
        "server_response_id",
        mode="before",
    )
    @classmethod
    def validate_optional_identifiers(cls, value, info):
        if value is None:
            return value
        if info.field_name == "server_response_id" and isinstance(value, str) and not value.strip():
            return None
        return cls.non_empty_string(value, info.field_name)

    @field_validator("compute_time_s", "solver_time_s", mode="before")
    @classmethod
    def validate_compute_time(cls, value, info):
        if value is None:
            return value
        return cls.finite_non_negative(value, info.field_name)

    @field_validator("credits_used", "credits_reserved", mode="before")
    @classmethod
    def validate_credit_counts(cls, value, info):
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
            raise ValueError(f"{info.field_name} must be a non-negative integer")
        return int(value)

    @property
    def normalized_status(self) -> JobStatus:
        """Return the backend status as a stable SDK lifecycle state."""

        return JobStatus.normalize(self.status)


class JobSubmission(PlatformModel):
    """Acknowledgement and job record returned after submission."""

    message: str = "Job submitted"
    expected_run_at: Optional[datetime] = None
    job: Job

    @field_validator("message", mode="before")
    @classmethod
    def validate_message(cls, value):
        return cls.non_empty_string(value, "message")


class JobList(PlatformModel):
    """Paginated platform jobs and optional aggregate usage."""

    jobs: List[Job] = Field(default_factory=list)
    pagination: Pagination = Field(default_factory=Pagination)
    total_compute_time_s: Optional[float] = None
    total_credits_used: Optional[float] = None

    @field_validator("total_compute_time_s", "total_credits_used", mode="before")
    @classmethod
    def validate_totals(cls, value, info):
        if value is None:
            return value
        return cls.finite_non_negative(value, info.field_name)


class CreditUsageJob(PlatformModel):
    """One job included in a credit-usage report."""

    job_id: str
    credits_used: int
    name: Optional[str] = None
    user_name: Optional[str] = None
    device_name: Optional[str] = None
    method: Optional[str] = None
    status: Optional[str] = None
    compute_resource_id: Optional[str] = None
    compute_backend: Optional[str] = None
    executed_at: Optional[datetime] = None

    @field_validator("job_id", mode="before")
    @classmethod
    def validate_job_id(cls, value):
        return cls.non_empty_string(value, "job_id")

    @field_validator(
        "name",
        "user_name",
        "device_name",
        "method",
        "status",
        "compute_resource_id",
        "compute_backend",
        mode="before",
    )
    @classmethod
    def validate_optional_text(cls, value, info):
        return cls.optional_non_empty_string(value, info.field_name)

    @field_validator("credits_used", mode="before")
    @classmethod
    def validate_credits_used(cls, value):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
            raise ValueError("credits_used must be a non-negative integer")
        return int(value)


class CreditUsage(PlatformModel):
    """Credit consumption for one user, workspace, or organization period."""

    used_credits: int
    start_date: datetime
    end_date: datetime
    jobs: List[CreditUsageJob] = Field(default_factory=list)

    @field_validator("used_credits", mode="before")
    @classmethod
    def validate_used_credits(cls, value):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
            raise ValueError("used_credits must be a non-negative integer")
        return int(value)

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be earlier than start_date")
        return self


class CreditResourceUsage(PlatformModel):
    """Credit consumption for one compute resource in a usage report."""

    compute_resource_id: str
    used_credits: int
    job_count: int
    compute_backend: Optional[str] = None

    @field_validator("compute_resource_id", mode="before")
    @classmethod
    def validate_resource_id(cls, value):
        return cls.non_empty_string(value, "compute_resource_id")

    @field_validator("compute_backend", mode="before")
    @classmethod
    def validate_backend(cls, value):
        return cls.optional_non_empty_string(value, "compute_backend")

    @field_validator("used_credits", "job_count", mode="before")
    @classmethod
    def validate_counts(cls, value, info):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
            raise ValueError(f"{info.field_name} must be a non-negative integer")
        return int(value)


class CreditUsageByResource(PlatformModel):
    """Credit consumption grouped by compute resource for one period."""

    used_credits: int
    start_date: datetime
    end_date: datetime
    compute_resource_usages: List[CreditResourceUsage] = Field(default_factory=list)

    @field_validator("used_credits", mode="before")
    @classmethod
    def validate_used_credits(cls, value):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
            raise ValueError("used_credits must be a non-negative integer")
        return int(value)

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be earlier than start_date")
        total = sum(item.used_credits for item in self.compute_resource_usages)
        if total != self.used_credits:
            raise ValueError("compute-resource usage does not sum to used_credits")
        return self


class CreditQuota(PlatformModel):
    """A current credit allocation and its remaining capacity."""

    id: str = Field(validation_alias=AliasChoices("id", "ID"), serialization_alias="id")
    compute_resource_id: str
    allocated_credits: int
    used_credits: int = 0
    reserved_credits: int = 0
    total_credits: Optional[int] = None
    top_up_credits: int = 0
    top_up_expiry_date: Optional[datetime] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    user_id: Optional[str] = None
    workspace_id: Optional[str] = None
    organization_id: Optional[str] = None
    compute_resource: Optional[dict[str, Any]] = None

    @field_validator("id", "compute_resource_id", mode="before")
    @classmethod
    def validate_required_ids(cls, value, info):
        return cls.non_empty_string(value, info.field_name)

    @field_validator("user_id", "workspace_id", "organization_id", mode="before")
    @classmethod
    def validate_optional_ids(cls, value, info):
        return cls.optional_non_empty_string(value, info.field_name)

    @field_validator(
        "allocated_credits",
        "used_credits",
        "reserved_credits",
        "total_credits",
        "top_up_credits",
        mode="before",
    )
    @classmethod
    def validate_credit_fields(cls, value, info):
        if value is None and info.field_name == "total_credits":
            return value
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
            raise ValueError(f"{info.field_name} must be a non-negative integer")
        return int(value)

    @field_validator("compute_resource", mode="before")
    @classmethod
    def validate_compute_resource(cls, value):
        if value is not None and not isinstance(value, dict):
            raise ValueError("compute_resource must be an object or None")
        return value

    @model_validator(mode="after")
    def validate_capacity(self):
        capacity = self.total_credits if self.total_credits is not None else self.allocated_credits
        if self.used_credits > capacity:
            raise ValueError("used_credits cannot exceed the quota capacity")
        if (
            self.to_date is not None
            and self.from_date is not None
            and self.to_date < self.from_date
        ):
            raise ValueError("to_date cannot be earlier than from_date")
        return self

    @property
    def capacity_credits(self) -> int:
        """Return the enforced credit capacity for this quota."""

        return self.total_credits if self.total_credits is not None else self.allocated_credits

    @property
    def remaining_credits(self) -> int:
        """Return capacity minus credits already charged by the platform."""

        return self.capacity_credits - self.used_credits


class ComputeResource(PlatformModel):
    """Discoverable solver deployment for advanced resource pinning."""

    id: str = Field(validation_alias="ID", serialization_alias="id")
    backend_name: Optional[str] = None
    type: Optional[str] = None
    device_name: Optional[str] = None
    status: Optional[str] = None
    usage_rate: Optional[float] = None

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, value):
        return cls.non_empty_string(value, "id")

    @field_validator("backend_name", "type", "device_name", "status", mode="before")
    @classmethod
    def validate_optional_text(cls, value, info):
        if value is None:
            return value
        return cls.non_empty_string(value, info.field_name)

    @field_validator("usage_rate", mode="before")
    @classmethod
    def validate_usage_rate(cls, value):
        if value is None:
            return value
        return cls.finite_non_negative(value, "usage_rate")


class SSEEvent(PlatformModel):
    """One decoded server-sent job event."""

    event: str
    data: Any

    @field_validator("event", mode="before")
    @classmethod
    def validate_event(cls, value):
        if not isinstance(value, str) or not value:
            raise ValueError("event must be a non-empty string")
        if "\r" in value or "\n" in value:
            raise ValueError("event cannot contain a newline")
        return value


class EntityModel(PlatformModel):
    """Base for platform entities identified by `id` or backend `ID`."""

    id: str = Field(
        validation_alias=AliasChoices("id", "ID"),
        serialization_alias="id",
    )

    @field_validator("id", mode="before")
    @classmethod
    def validate_entity_id(cls, value):
        return cls.non_empty_string(value, "id")


class UserSummary(EntityModel):
    """User membership summary returned inside collaboration records."""

    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    organization_id: Optional[str] = None
    is_revoked: Optional[bool] = None

    @field_validator("name", "email", "role", mode="before")
    @classmethod
    def validate_optional_text(cls, value, info):
        if value is None:
            return value
        return cls.non_empty_string(value, info.field_name)

    @field_validator("status", "organization_id", mode="before")
    @classmethod
    def normalize_unselected_text(cls, value, info):
        return cls.optional_non_empty_string(value, info.field_name)

    @field_validator("is_revoked", mode="before")
    @classmethod
    def validate_optional_boolean(cls, value):
        if value is not None and not isinstance(value, bool):
            raise ValueError("is_revoked must be a boolean")
        return value


class Organization(EntityModel):
    """Organization, limits, and member summaries."""

    organization_name: str
    billing_details: Optional[str] = None
    min_booking_minutes: Optional[int] = Field(default=None, strict=True, ge=0)
    max_booking_minutes: Optional[int] = Field(default=None, strict=True, ge=0)
    max_daily_minutes: Optional[int] = Field(default=None, strict=True, ge=0)
    current_seat_count: Optional[int] = Field(default=None, strict=True, ge=0)
    users: List[UserSummary] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("organization_name", mode="before")
    @classmethod
    def validate_organization_name(cls, value):
        return cls.non_empty_string(value, "organization_name")

    @model_validator(mode="after")
    def validate_booking_range(self):
        if (
            self.min_booking_minutes is not None
            and self.max_booking_minutes is not None
            and self.min_booking_minutes > self.max_booking_minutes
        ):
            raise ValueError("min_booking_minutes cannot exceed max_booking_minutes")
        return self


class Workspace(EntityModel):
    """Workspace record."""

    owner_id: Optional[str] = None
    name: str
    description: str = ""
    organization_id: str
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("owner_id", "name", "organization_id", mode="before")
    @classmethod
    def validate_workspace_text(cls, value, info):
        if value is None and info.field_name == "owner_id":
            return value
        return cls.non_empty_string(value, info.field_name)

    @field_validator("is_default", mode="before")
    @classmethod
    def validate_default_flag(cls, value):
        if not isinstance(value, bool):
            raise ValueError("is_default must be a boolean")
        return value


class Project(EntityModel):
    """Project record."""

    owner_id: str
    name: str
    description: str = ""
    workspace_id: str
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("owner_id", "name", "workspace_id", mode="before")
    @classmethod
    def validate_project_text(cls, value, info):
        return cls.non_empty_string(value, info.field_name)

    @field_validator("is_default", mode="before")
    @classmethod
    def validate_default_flag(cls, value):
        if not isinstance(value, bool):
            raise ValueError("is_default must be a boolean")
        return value


class Experiment(EntityModel):
    """Experiment record and aggregate usage."""

    owner_id: str
    name: str
    description: str = ""
    project_id: str
    workspace_id: Optional[str] = None
    compute_resource_id: Optional[str] = None
    is_default: bool = False
    total_compute_time_s: float = 0.0
    total_credits_used: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator(
        "owner_id",
        "name",
        "project_id",
        "workspace_id",
        "compute_resource_id",
        mode="before",
    )
    @classmethod
    def validate_experiment_text(cls, value, info):
        if info.field_name == "workspace_id":
            return cls.optional_non_empty_string(value, info.field_name)
        if value is None and info.field_name == "compute_resource_id":
            return value
        return cls.non_empty_string(value, info.field_name)

    @field_validator("is_default", mode="before")
    @classmethod
    def validate_default_flag(cls, value):
        if not isinstance(value, bool):
            raise ValueError("is_default must be a boolean")
        return value

    @field_validator("total_compute_time_s", mode="before")
    @classmethod
    def validate_compute_time(cls, value):
        return cls.finite_non_negative(value, "total_compute_time_s")

    @field_validator("total_credits_used", mode="before")
    @classmethod
    def validate_credits(cls, value):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
            raise ValueError("total_credits_used must be a non-negative integer")
        return int(value)


class Invitation(EntityModel):
    """Organization invitation record."""

    organization_id: str
    email: str
    role: str
    status: str
    invitation_type: Optional[str] = None
    invited_by: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("organization_id", "email", "role", "status", mode="before")
    @classmethod
    def validate_required_text(cls, value, info):
        return cls.non_empty_string(value, info.field_name)

    @field_validator("invitation_type", "invited_by", mode="before")
    @classmethod
    def validate_optional_text(cls, value, info):
        if value is None:
            return value
        return cls.non_empty_string(value, info.field_name)


class WorkspaceList(PlatformModel):
    """Paginated workspace records."""

    workspaces: List[Workspace] = Field(default_factory=list)
    pagination: Pagination = Field(default_factory=Pagination)
    summary: Any = None


class ProjectList(PlatformModel):
    """Paginated project records."""

    projects: List[Project] = Field(default_factory=list)
    pagination: Pagination = Field(default_factory=Pagination)


class ExperimentList(PlatformModel):
    """Paginated experiment records."""

    experiments: List[Experiment] = Field(default_factory=list)
    pagination: Pagination = Field(default_factory=Pagination)
