"""Authenticated QpiAI Platform HTTP client and lifecycle operations."""

from __future__ import annotations

import json
import numbers
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Union, cast
from urllib.parse import urlsplit

import requests

from .._numeric import is_finite_real
from .exceptions import APIError, AuthenticationError, JobCancelledError, JobFailedError
from .platform_models import (
    ComputeResource,
    CreditQuota,
    CreditUsage,
    CreditUsageByResource,
    Experiment,
    ExperimentList,
    Invitation,
    Job,
    JobList,
    JobStatus,
    JobSubmission,
    Organization,
    Problem,
    ProblemList,
    Project,
    ProjectList,
    SSEEvent,
    UserSummary,
    Workspace,
    WorkspaceList,
)

API_KEY_ERROR = (
    "API key is missing / invalid. Get a valid API key by registering at https://opt.qpiai.tech/"
)


class Client:
    """Authenticated client for QpiAI Platform resources, jobs, and storage."""

    DEFAULT_URL = "https://opt-server.qpiai.tech/api"
    DEFAULT_EXPERIMENT_NAME = "Default Experiment"
    _PATH_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9._~-]+")
    _JOB_LIST_PARAM_NAMES = frozenset(
        {"page", "page_size", "period", "status", "compute_resource_id"}
    )
    _JOB_STATUS_FILTERS = {
        JobStatus.PENDING: "Pending",
        JobStatus.RUNNING: "Running",
        JobStatus.COMPLETED: "Success",
        JobStatus.FAILED: "Failed",
        JobStatus.CANCELLED: "Cancelled",
    }

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        experiment_id: Optional[str] = None,
        experiment_name: Optional[str] = None,
        base_url: str = DEFAULT_URL,
        timeout: float = 60.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError(API_KEY_ERROR)
        base_url = self.validate_base_url(base_url)
        api_key = api_key.strip()
        if experiment_id is not None:
            experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        if experiment_name is not None:
            experiment_name = self.validate_identifier(experiment_name, "experiment_name")
        timeout = self.validate_timeout(timeout, "timeout")
        self.base_url = base_url
        self.api_key = api_key
        self.experiment_id = experiment_id
        self.experiment_name = (
            experiment_name if experiment_name is not None else self.DEFAULT_EXPERIMENT_NAME
        )
        self._experiment_ids: Dict[str, str] = {}
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()

    @staticmethod
    def validate_identifier(value: str, name: str) -> str:
        """Return a stripped non-empty identifier."""

        if not isinstance(value, str) or not value.strip():
            if name == "api_key":
                raise ValueError(API_KEY_ERROR)
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    @classmethod
    def _validate_path_identifier(cls, value: str, name: str) -> str:
        """Return one platform identifier that cannot alter an API route."""

        normalized = cls.validate_identifier(value, name)
        if normalized in {".", ".."} or cls._PATH_IDENTIFIER_PATTERN.fullmatch(normalized) is None:
            raise ValueError(
                f"{name} must contain only A-Z, a-z, 0-9, '.', '_', '-', or '~', "
                "and cannot be '.' or '..'"
            )
        return normalized

    @staticmethod
    def validate_base_url(value: str) -> str:
        """Validate and normalize an HTTP(S) API base URL."""

        if not isinstance(value, str) or not value.strip():
            raise ValueError("base_url must be a non-empty HTTP(S) URL")
        normalized = value.strip()
        parsed_url = urlsplit(normalized)
        if (
            parsed_url.scheme.lower() not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("base_url must be an HTTP(S) URL without a query or fragment")
        return normalized.rstrip("/")

    @staticmethod
    def validate_pagination(page: int, page_size: int) -> tuple[int, int]:
        """Validate positive page and page-size values."""

        for value, name in ((page, "page"), (page_size, "page_size")):
            if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        return int(page), int(page_size)

    @classmethod
    def _validated_job_list_params(cls, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validate filters accepted by problem and experiment job listings."""

        unknown = sorted(set(params) - cls._JOB_LIST_PARAM_NAMES)
        if unknown:
            raise ValueError("Unsupported job-list parameters: " + ", ".join(unknown))

        validated = dict(params)
        if "page" in validated or "page_size" in validated:
            page, page_size = cls.validate_pagination(
                validated.get("page", 1),
                validated.get("page_size", 10),
            )
            validated.update({"page": page, "page_size": page_size})
        for name in ("period", "compute_resource_id"):
            if validated.get(name) is not None:
                validated[name] = cls.validate_identifier(validated[name], name)
        if validated.get("status") is not None:
            validated["status"] = cls._job_status_filter(validated["status"])
        return validated

    @classmethod
    def _job_status_filter(cls, value: JobStatus | str) -> str:
        """Translate one normalized SDK job status to the platform query value."""

        if isinstance(value, JobStatus):
            status = value
        elif isinstance(value, str):
            try:
                status = JobStatus(value.strip())
            except ValueError:
                status = None
        else:
            status = None
        if status not in cls._JOB_STATUS_FILTERS:
            choices = ", ".join(repr(item.value) for item in cls._JOB_STATUS_FILTERS)
            raise ValueError(f"status must be one of {choices}")
        return cls._JOB_STATUS_FILTERS[status]

    @staticmethod
    def validate_boolean(value: bool, name: str) -> bool:
        """Validate a strict Boolean option."""

        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        return value

    @staticmethod
    def validate_scheduled_at(value: str) -> str:
        """Return a timezone-aware ISO 8601 schedule timestamp."""

        value = Client.validate_identifier(value, "scheduled_at")
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        except ValueError as exc:
            raise ValueError("scheduled_at must be an ISO 8601 timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone offset")
        return value

    @staticmethod
    def validate_timeout(value: float, name: str = "timeout") -> float:
        """Validate a finite positive timeout."""

        if not is_finite_real(value) or value <= 0:
            raise ValueError(f"{name} must be a finite positive number")
        return float(value)

    @classmethod
    def _validate_request_timeout(cls, value):
        if isinstance(value, tuple):
            if len(value) != 2:
                raise ValueError("request timeout tuple must contain connect and read timeouts")
            return tuple(
                cls.validate_timeout(part, description)
                for part, description in zip(value, ("connect timeout", "read timeout"))
            )
        return cls.validate_timeout(value, "request timeout")

    @property
    def headers(self) -> Dict[str, str]:
        """Return authentication headers for platform requests."""

        return {"X-Secret-Token": self.api_key}

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}))
        timeout = self._validate_request_timeout(kwargs.pop("timeout", self.timeout))
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            timeout=timeout,
            **kwargs,
        )
        if response.status_code >= 400:
            self._raise_api_error(response)
        return response

    @staticmethod
    def _raise_api_error(response: requests.Response) -> None:
        try:
            body = response.json()
        except ValueError:
            body = {"error": response.text or response.reason}
        if not isinstance(body, dict):
            body = {"error": response.text or response.reason, "details": body}

        message = None
        for key in ("error", "message", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                message = value
                break
            if isinstance(value, dict):
                nested = value.get("message") or value.get("detail") or value.get("error")
                if isinstance(nested, str) and nested.strip():
                    message = nested
                    break
        message = message or response.text or response.reason
        if response.status_code == 401 and body.get("code") != "ACCOUNT_REVOKED":
            message = API_KEY_ERROR
        error_type = AuthenticationError if response.status_code == 401 else APIError
        raise error_type(
            message,
            status_code=response.status_code,
            code=body.get("code"),
            details=body.get("details") or body.get("data"),
        )

    @staticmethod
    def _response_json(response: requests.Response, context: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise APIError(
                f"{context} returned invalid JSON",
                status_code=response.status_code,
            ) from exc

    @classmethod
    def _response_object(cls, response: requests.Response, context: str) -> Dict[str, Any]:
        data = cls._response_json(response, context)
        if not isinstance(data, dict):
            raise APIError(
                f"{context} returned {type(data).__name__}; expected a JSON object",
                status_code=response.status_code,
            )
        return data

    @classmethod
    def _response_array(cls, response: requests.Response, context: str) -> List[Any]:
        data = cls._response_json(response, context)
        if not isinstance(data, list):
            raise APIError(
                f"{context} returned {type(data).__name__}; expected a JSON array",
                status_code=response.status_code,
            )
        return data

    def _request_object(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self._response_object(self._request(method, path, **kwargs), f"{method} {path}")

    def _request_array(self, method: str, path: str, **kwargs: Any) -> List[Any]:
        return self._response_array(self._request(method, path, **kwargs), f"{method} {path}")

    def verify_api_key(self, api_key: Optional[str] = None) -> Dict[str, Any]:
        """Verify an API key without changing the client's stored credential."""

        value = self.api_key if api_key is None else api_key
        if not isinstance(value, str) or not value.strip():
            raise ValueError(API_KEY_ERROR)
        value = value.strip()
        response = self.session.request(
            "POST",
            f"{self.base_url}/users/verify_secret_access_token",
            json={"secret_access_token": value},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            self._raise_api_error(response)
        return self._response_object(response, "API-key verification")

    def me(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Return the authenticated user record."""

        request_options = {} if timeout is None else {"timeout": timeout}
        return self._response_object(
            self._request("GET", "/users/me", **request_options), "Current-user request"
        )

    def get_api_key(self) -> Dict[str, Any]:
        """Return the current access-token record."""

        return self._response_object(
            self._request("GET", "/users/get-access-token"), "Access-token request"
        )

    def regenerate_api_key(self) -> Dict[str, Any]:
        """Regenerate and return the access-token record."""

        return self._response_object(
            self._request("POST", "/users/regenerate-token"), "Access-token regeneration"
        )

    def _current_user_id(self, *, timeout: Optional[float] = None) -> str:
        data = self.me(timeout=timeout)
        user_id = data.get("id") or data.get("ID")
        if not user_id:
            raise APIError("Current-user response did not include an ID")
        return self._validate_path_identifier(user_id, "current user ID")

    def _current_organization_id(self, *, timeout: Optional[float] = None) -> str:
        data = self.me(timeout=timeout)
        organization_id = data.get("organization_id")
        if not organization_id:
            raise APIError("Current-user response did not include an organization ID")
        return self._validate_path_identifier(organization_id, "current organization ID")

    @staticmethod
    def _validate_credit_period(period: str) -> Literal["daily", "weekly", "monthly", "yearly"]:
        if not isinstance(period, str) or period.strip().lower() not in {
            "daily",
            "weekly",
            "monthly",
            "yearly",
        }:
            raise ValueError("period must be 'daily', 'weekly', 'monthly', or 'yearly'")
        return cast(Literal["daily", "weekly", "monthly", "yearly"], period.strip().lower())

    @staticmethod
    def _validate_by_resource(value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError("by_resource must be a boolean")
        return value

    def _credit_usage(
        self,
        path: str,
        *,
        period: str,
        by_resource: bool,
        timeout: Optional[float],
    ) -> Union[CreditUsage, CreditUsageByResource]:
        period = self._validate_credit_period(period)
        by_resource = self._validate_by_resource(by_resource)
        request_options = {} if timeout is None else {"timeout": timeout}
        data = self._request_object(
            "GET",
            path,
            params={"period": period},
            **request_options,
        )
        if by_resource:
            return CreditUsageByResource.model_validate(data)
        return CreditUsage.model_validate(data)

    def get_user_credit_usage(
        self,
        user_id: Optional[str] = None,
        *,
        period: str = "monthly",
        by_resource: bool = False,
        timeout: Optional[float] = None,
    ) -> Union[CreditUsage, CreditUsageByResource]:
        """Return credit usage for a user and period.

        Omitting ``user_id`` requests the authenticated user's usage. Set
        ``by_resource=True`` for the per-compute-resource breakdown.
        """

        user_id = (
            self._current_user_id(timeout=timeout)
            if user_id is None
            else self._validate_path_identifier(user_id, "user_id")
        )
        path = f"/credit-usage/user/{user_id}"
        if by_resource:
            path += "/by-resource"
        return self._credit_usage(
            path,
            period=period,
            by_resource=by_resource,
            timeout=timeout,
        )

    def get_workspace_credit_usage(
        self,
        workspace_id: str,
        *,
        period: str = "monthly",
        by_resource: bool = False,
        timeout: Optional[float] = None,
    ) -> Union[CreditUsage, CreditUsageByResource]:
        """Return credit usage for a workspace and period."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        path = f"/credit-usage/workspace/{workspace_id}"
        if by_resource:
            path += "/by-resource"
        return self._credit_usage(
            path,
            period=period,
            by_resource=by_resource,
            timeout=timeout,
        )

    def get_organization_credit_usage(
        self,
        organization_id: Optional[str] = None,
        *,
        period: str = "monthly",
        by_resource: bool = False,
        timeout: Optional[float] = None,
    ) -> Union[CreditUsage, CreditUsageByResource]:
        """Return credit usage for an organization and period.

        Omitting ``organization_id`` requests the authenticated user's
        organization. Authorization is enforced by the platform.
        """

        organization_id = (
            self._current_organization_id(timeout=timeout)
            if organization_id is None
            else self._validate_path_identifier(organization_id, "organization_id")
        )
        path = f"/credit-usage/organization/{organization_id}"
        if by_resource:
            path += "/by-resource"
        return self._credit_usage(
            path,
            period=period,
            by_resource=by_resource,
            timeout=timeout,
        )

    @staticmethod
    def _quota_list(data: Dict[str, Any], context: str) -> List[CreditQuota]:
        values = data.get("quotas")
        if not isinstance(values, list):
            raise APIError(f"{context} returned an invalid quotas collection")
        return [CreditQuota.model_validate(value) for value in values]

    def get_user_quotas(
        self,
        user_id: Optional[str] = None,
        *,
        timeout: Optional[float] = None,
    ) -> List[CreditQuota]:
        """Return credit allocations for a user, including remaining credits."""

        user_id = (
            self._current_user_id(timeout=timeout)
            if user_id is None
            else self._validate_path_identifier(user_id, "user_id")
        )
        request_options = {} if timeout is None else {"timeout": timeout}
        data = self._request_array(
            "GET",
            f"/user-quotas/user/{user_id}",
            **request_options,
        )
        return [CreditQuota.model_validate(value) for value in data]

    def get_workspace_quotas(
        self,
        workspace_id: str,
        *,
        timeout: Optional[float] = None,
    ) -> List[CreditQuota]:
        """Return credit allocations for a workspace."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        request_options = {} if timeout is None else {"timeout": timeout}
        data = self._request_object(
            "GET",
            f"/workspace-quotas/workspace/{workspace_id}",
            **request_options,
        )
        return self._quota_list(data, "Workspace quota request")

    def get_organization_quotas(
        self,
        organization_id: Optional[str] = None,
        *,
        timeout: Optional[float] = None,
    ) -> List[CreditQuota]:
        """Return credit allocations for an organization."""

        organization_id = (
            self._current_organization_id(timeout=timeout)
            if organization_id is None
            else self._validate_path_identifier(organization_id, "organization_id")
        )
        request_options = {} if timeout is None else {"timeout": timeout}
        data = self._request_object(
            "GET",
            f"/organization-quotas/organization/{organization_id}",
            **request_options,
        )
        return self._quota_list(data, "Organization quota request")

    # Organization and membership
    def get_organization(self, organization_id: str) -> Organization:
        """Return an organization by ID."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        return Organization.model_validate(
            self._request_object("GET", f"/organizations/{organization_id}")
        )

    def update_organization(self, organization_id: str, *, name: str) -> Dict[str, Any]:
        """Rename an organization and return the platform confirmation."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        name = self.validate_identifier(name, "name")
        return self._request_object(
            "PUT", f"/organizations/{organization_id}", json={"organization_name": name}
        )

    def delete_organization(self, organization_id: str) -> Dict[str, Any]:
        """Delete an organization and return the platform confirmation."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        return self._request_object("DELETE", f"/organizations/{organization_id}")

    def invite_member(self, organization_id: str, *, email: str, role: str) -> Dict[str, Any]:
        """Invite one organization member with the requested role."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        email = self.validate_identifier(email, "email")
        role = self.validate_identifier(role, "role")
        return self._request_object(
            "POST",
            "/organizations/invite",
            json={"organization_id": organization_id, "email": email, "role": role},
        )

    def invite_members(self, organization_id: str, users: List[Dict[str, str]]) -> Dict[str, Any]:
        """Invite a nonempty batch of organization members."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        if not isinstance(users, list) or not users:
            raise ValueError("users must be a non-empty list")
        normalized = []
        for index, user in enumerate(users):
            if not isinstance(user, dict):
                raise ValueError(f"users[{index}] must be a dictionary")
            unknown = sorted(set(user) - {"email", "role"})
            if unknown:
                raise ValueError(
                    f"users[{index}] contains unsupported fields: " + ", ".join(unknown)
                )
            normalized.append(
                {
                    "email": self.validate_identifier(user.get("email"), f"users[{index}].email"),
                    "role": self.validate_identifier(user.get("role"), f"users[{index}].role"),
                }
            )
        return self._request_object(
            "POST",
            "/organizations/invite-bulk",
            json={"organization_id": organization_id, "users": normalized},
        )

    def list_invitations(self, organization_id: str) -> List[Invitation]:
        """Return invitations for an organization."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        data = self._request_array("GET", f"/organizations/{organization_id}/all-invitations")
        return [Invitation.model_validate(item) for item in data]

    def cancel_invitation(self, invitation_id: str) -> Dict[str, Any]:
        """Cancel an invitation and return the platform confirmation."""

        invitation_id = self._validate_path_identifier(invitation_id, "invitation_id")
        return self._request_object("DELETE", f"/invitations/cancel/{invitation_id}")

    def update_member_role(
        self, organization_id: str, user_id: str, *, role: str
    ) -> Dict[str, Any]:
        """Update an organization member's role."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        user_id = self._validate_path_identifier(user_id, "user_id")
        role = self.validate_identifier(role, "role")
        return self._request_object(
            "PUT",
            f"/organizations/{organization_id}/members/{user_id}/role",
            json={"role": role},
        )

    def can_invite_members(self, organization_id: str) -> Dict[str, Any]:
        """Return invitation capacity and permission data."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        return self._request_object("GET", f"/organizations/{organization_id}/can-invite-users")

    def get_organization_statistics(self, organization_id: str) -> Dict[str, Any]:
        """Return aggregate organization statistics."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        return self._request_object("GET", f"/organizations/{organization_id}/statistics")

    def get_organization_compute_resources(self, organization_id: str) -> List[ComputeResource]:
        """Return compute resources visible to an organization."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        data = self._request_object("GET", f"/organizations/{organization_id}/compute-resources")
        resources = data.get("compute_resources")
        if not isinstance(resources, list):
            raise APIError(
                "Organization compute-resource response did not include a compute_resources array"
            )
        return [ComputeResource.model_validate(item) for item in resources]

    # Workspaces
    def create_workspace(
        self,
        organization_id: str,
        *,
        name: str,
        description: str = "",
    ) -> Workspace:
        """Create and return a workspace in an organization."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        name = self.validate_identifier(name, "name")
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        owner_id = self._current_user_id()
        return Workspace.model_validate(
            self._request_object(
                "POST",
                "/workspaces/create",
                json={
                    "owner_id": owner_id,
                    "name": name,
                    "description": description,
                    "organization_id": organization_id,
                },
            )
        )

    def get_workspace(self, workspace_id: str) -> Workspace:
        """Return a workspace by ID."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        return Workspace.model_validate(self._request_object("GET", f"/workspaces/{workspace_id}"))

    def update_workspace(self, workspace_id: str, *, name: str, description: str = "") -> Workspace:
        """Update and return a workspace."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        name = self.validate_identifier(name, "name")
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        current = self.get_workspace(workspace_id)
        data = self._request_object(
            "PUT", f"/workspaces/{workspace_id}", json={"name": name, "description": description}
        )
        return Workspace.model_validate({**current.model_dump(), **data, "id": workspace_id})

    def delete_workspace(self, workspace_id: str) -> Dict[str, Any]:
        """Delete a workspace and return the platform confirmation."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        return self._request_object("DELETE", f"/workspaces/{workspace_id}")

    def list_workspaces(
        self, organization_id: str, *, page: int = 1, page_size: int = 10
    ) -> WorkspaceList:
        """Return a page of workspaces for an organization."""

        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        page, page_size = self.validate_pagination(page, page_size)
        return WorkspaceList.model_validate(
            self._request_object(
                "GET",
                f"/workspaces/organization/{organization_id}",
                params={"page": page, "page_size": page_size},
            )
        )

    def list_my_workspaces(
        self, *, page: int = 1, page_size: int = 10, search: Optional[str] = None
    ) -> WorkspaceList:
        """Return a page of workspaces visible to the authenticated user."""

        page, page_size = self.validate_pagination(page, page_size)
        params: Dict[str, Any] = {"page": page, "page_size": page_size}
        if search is not None:
            params["search"] = self.validate_identifier(search, "search")
        return WorkspaceList.model_validate(
            self._request_object("GET", "/workspaces/my-workspaces", params=params)
        )

    def list_workspace_members(self, workspace_id: str) -> List[UserSummary]:
        """Return members of a workspace."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        return [
            UserSummary.model_validate(item)
            for item in self._request_array("GET", f"/workspaces/{workspace_id}/users")
        ]

    def add_workspace_member(
        self, workspace_id: str, *, user_id: Optional[str] = None, email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add one workspace member by user ID or email."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        if (user_id is None) == (email is None):
            raise ValueError("Provide exactly one of user_id or email")
        if user_id is not None:
            path = f"/workspaces/{workspace_id}/users/add"
            payload = {"user_id": self._validate_path_identifier(user_id, "user_id")}
        else:
            path = f"/workspaces/{workspace_id}/users/add-by-email"
            payload = {"email": self.validate_identifier(email, "email")}
        return self._request_object("POST", path, json=payload)

    def remove_workspace_member(self, workspace_id: str, user_id: str) -> Dict[str, Any]:
        """Remove one workspace member."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        user_id = self._validate_path_identifier(user_id, "user_id")
        return self._request_object(
            "DELETE", f"/workspaces/{workspace_id}/users/remove", json={"user_id": user_id}
        )

    def add_workspace_members(self, workspace_id: str, emails: List[str]) -> Dict[str, Any]:
        """Add a nonempty batch of workspace members by email."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        if not isinstance(emails, list) or not emails:
            raise ValueError("emails must be a non-empty list")
        users = [
            {"email": self.validate_identifier(email, f"emails[{index}]")}
            for index, email in enumerate(emails)
        ]
        return self._request_object(
            "POST",
            f"/workspaces/{workspace_id}/users/bulk-add",
            json={"users": users},
        )

    # Projects
    def create_project(self, workspace_id: str, *, name: str, description: str = "") -> Project:
        """Create and return a project in a workspace."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        name = self.validate_identifier(name, "name")
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        return Project.model_validate(
            self._request_object(
                "POST",
                "/projects/create",
                json={"name": name, "description": description, "workspace_id": workspace_id},
            )
        )

    def get_project(self, project_id: str) -> Project:
        """Return a project by ID."""

        project_id = self._validate_path_identifier(project_id, "project_id")
        return Project.model_validate(self._request_object("GET", f"/projects/{project_id}"))

    def update_project(self, project_id: str, *, name: str, description: str = "") -> Project:
        """Update and return a project."""

        project_id = self._validate_path_identifier(project_id, "project_id")
        name = self.validate_identifier(name, "name")
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        current = self.get_project(project_id)
        data = self._request_object(
            "PUT", f"/projects/{project_id}", json={"name": name, "description": description}
        )
        return Project.model_validate({**current.model_dump(), **data, "id": project_id})

    def delete_project(self, project_id: str) -> Dict[str, Any]:
        """Delete a project and return the platform confirmation."""

        project_id = self._validate_path_identifier(project_id, "project_id")
        return self._request_object("DELETE", f"/projects/{project_id}")

    def list_projects(
        self,
        workspace_id: str,
        *,
        page: int = 1,
        page_size: int = 10,
        search: Optional[str] = None,
    ) -> ProjectList:
        """Return a page of projects in a workspace."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        page, page_size = self.validate_pagination(page, page_size)
        params: Dict[str, Any] = {"page": page, "page_size": page_size}
        if search is not None:
            params["search"] = self.validate_identifier(search, "search")
        return ProjectList.model_validate(
            self._request_object("GET", f"/projects/workspace/{workspace_id}", params=params)
        )

    def list_my_projects(self, *, page: int = 1, page_size: int = 10) -> ProjectList:
        """Return a page of projects visible to the authenticated user."""

        page, page_size = self.validate_pagination(page, page_size)
        return ProjectList.model_validate(
            self._request_object(
                "GET", "/projects/my", params={"page": page, "page_size": page_size}
            )
        )

    def list_project_members(self, project_id: str) -> List[UserSummary]:
        """Return members of a project."""

        project_id = self._validate_path_identifier(project_id, "project_id")
        return [
            UserSummary.model_validate(item)
            for item in self._request_array("GET", f"/projects/{project_id}/users")
        ]

    def add_project_member(
        self, project_id: str, *, user_id: Optional[str] = None, email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add one project member by user ID or email."""

        project_id = self._validate_path_identifier(project_id, "project_id")
        if (user_id is None) == (email is None):
            raise ValueError("Provide exactly one of user_id or email")
        if user_id is not None:
            path = f"/projects/{project_id}/users/add"
            payload = {"user_id": self._validate_path_identifier(user_id, "user_id")}
        else:
            path = f"/projects/{project_id}/users/add-by-email"
            payload = {"email": self.validate_identifier(email, "email")}
        return self._request_object("POST", path, json=payload)

    def remove_project_member(self, project_id: str, user_id: str) -> Dict[str, Any]:
        """Remove one project member."""

        project_id = self._validate_path_identifier(project_id, "project_id")
        user_id = self._validate_path_identifier(user_id, "user_id")
        return self._request_object(
            "DELETE", f"/projects/{project_id}/users/remove", json={"user_id": user_id}
        )

    # Experiments
    def create_experiment(self, project_id: str, *, name: str, description: str = "") -> Experiment:
        """Create and return an experiment in a project."""

        project_id = self._validate_path_identifier(project_id, "project_id")
        name = self.validate_identifier(name, "name")
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        owner_id = self._current_user_id()
        result = self._request_object(
            "POST",
            "/experiments/create",
            json={
                "owner_id": owner_id,
                "name": name,
                "description": description,
                "project_id": project_id,
            },
        )
        return Experiment.model_validate(result)

    def get_experiment(self, experiment_id: str) -> Experiment:
        """Return an experiment by ID."""

        experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        return Experiment.model_validate(
            self._request_object("GET", f"/experiments/{experiment_id}")
        )

    def update_experiment(
        self, experiment_id: str, *, name: str, description: str = ""
    ) -> Experiment:
        """Update and return an experiment."""

        experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        name = self.validate_identifier(name, "name")
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        current = self.get_experiment(experiment_id)
        data = self._request_object(
            "PUT",
            f"/experiments/{experiment_id}",
            json={"name": name, "description": description},
        )
        return Experiment.model_validate({**current.model_dump(), **data, "id": experiment_id})

    def delete_experiment(self, experiment_id: str) -> Dict[str, Any]:
        """Delete an experiment and return the platform confirmation."""

        experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        return self._request_object("DELETE", f"/experiments/{experiment_id}")

    def list_experiments(
        self,
        project_id: str,
        *,
        page: int = 1,
        page_size: int = 10,
        search: Optional[str] = None,
    ) -> ExperimentList:
        """Return a page of experiments in a project."""

        project_id = self._validate_path_identifier(project_id, "project_id")
        page, page_size = self.validate_pagination(page, page_size)
        params: Dict[str, Any] = {"page": page, "page_size": page_size}
        if search is not None:
            params["search"] = self.validate_identifier(search, "search")
        return ExperimentList.model_validate(
            self._request_object("GET", f"/experiments/project/{project_id}", params=params)
        )

    def list_my_experiments(self, *, page: int = 1, page_size: int = 10) -> ExperimentList:
        """Return a page of experiments visible to the authenticated user."""

        page, page_size = self.validate_pagination(page, page_size)
        return ExperimentList.model_validate(
            self._request_object(
                "GET", "/experiments/my", params={"page": page, "page_size": page_size}
            )
        )

    def list_workspace_experiments(
        self, workspace_id: str, *, page: int = 1, page_size: int = 10
    ) -> ExperimentList:
        """Return a page of experiments in a workspace."""

        workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        page, page_size = self.validate_pagination(page, page_size)
        return ExperimentList.model_validate(
            self._request_object(
                "GET",
                f"/experiments/workspace/{workspace_id}",
                params={"page": page, "page_size": page_size},
            )
        )

    def list_experiment_members(self, experiment_id: str) -> List[UserSummary]:
        """Return members of an experiment."""

        experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        return [
            UserSummary.model_validate(item)
            for item in self._request_array("GET", f"/experiments/{experiment_id}/users")
        ]

    def add_experiment_member(self, experiment_id: str, user_id: str) -> Dict[str, Any]:
        """Add one experiment member by user ID."""

        experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        user_id = self._validate_path_identifier(user_id, "user_id")
        return self._request_object(
            "POST", f"/experiments/{experiment_id}/users/add", json={"user_id": user_id}
        )

    def remove_experiment_member(self, experiment_id: str, user_id: str) -> Dict[str, Any]:
        """Remove one experiment member."""

        experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        user_id = self._validate_path_identifier(user_id, "user_id")
        return self._request_object(
            "DELETE",
            f"/experiments/{experiment_id}/users/remove",
            json={"user_id": user_id},
        )

    def associate_compute_resource(
        self, experiment_id: str, compute_resource_id: str
    ) -> Dict[str, Any]:
        """Associate an experiment with a discovered compute resource."""

        experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        compute_resource_id = self._validate_path_identifier(
            compute_resource_id, "compute_resource_id"
        )
        return self._request_object(
            "POST",
            f"/experiments/{experiment_id}/associate-compute-resource",
            json={"compute_resource_id": compute_resource_id},
        )

    @staticmethod
    def default_problem_name(method: str, detail: Optional[str] = None) -> str:
        """Return a UTC-timestamped default problem name."""

        method = Client.validate_identifier(method, "method")
        if detail is not None:
            detail = Client.validate_identifier(detail, "detail")
        timestamp = (
            datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        prefix = f"SDK {method.upper()} problem"
        if detail:
            prefix = f"{prefix}: {detail}"
        return f"{prefix} - {timestamp}"

    def resolve_experiment(
        self,
        name: str,
        *,
        timeout: Optional[float] = None,
    ) -> str:
        """Resolve an exact experiment name within the authenticated organization."""
        normalized_name = self.validate_identifier(name, "experiment name")
        if normalized_name not in self._experiment_ids:
            request_options = {} if timeout is None else {"timeout": timeout}
            data = self._request_object(
                "GET",
                "/experiments/by-name",
                params={"name": normalized_name},
                **request_options,
            )
            experiment_id = data.get("experiment_id")
            if not experiment_id:
                raise APIError("Experiment lookup response did not include an experiment ID")
            self._experiment_ids[normalized_name] = self._validate_path_identifier(
                experiment_id,
                "experiment_id",
            )
        return self._experiment_ids[normalized_name]

    def _select_experiment(
        self,
        experiment_id: Optional[str],
        experiment_name: Optional[str],
        *,
        timeout: Optional[float] = None,
    ) -> str:
        if experiment_id is not None:
            return self._validate_path_identifier(experiment_id, "experiment_id")
        if experiment_name is not None:
            return self.resolve_experiment(
                self.validate_identifier(experiment_name, "experiment_name"),
                timeout=timeout,
            )
        if self.experiment_id is not None:
            return self.experiment_id
        if self.experiment_name is not None:
            return self.resolve_experiment(self.experiment_name, timeout=timeout)
        return self.resolve_experiment(self.DEFAULT_EXPERIMENT_NAME, timeout=timeout)

    def create_problem(
        self,
        payload: Dict[str, Any],
        *,
        timeout: Optional[float] = None,
    ) -> Problem:
        """Create and return a validated platform problem record."""

        if not isinstance(payload, dict):
            raise ValueError("problem payload must be a dictionary")
        payload = dict(payload)
        for field in ("name", "method"):
            if field not in payload:
                raise ValueError(f"problem payload must include {field}")
            payload[field] = self.validate_identifier(payload[field], f"problem {field}")
        request_options = {} if timeout is None else {"timeout": timeout}
        return Problem.model_validate(
            self._request_object(
                "POST",
                "/problems/create",
                json=payload,
                **request_options,
            )
        )

    def get_problem(self, problem_id: str, *, timeout: Optional[float] = None) -> Problem:
        """Return a platform problem by ID."""

        problem_id = self._validate_path_identifier(problem_id, "problem_id")
        request_options = {} if timeout is None else {"timeout": timeout}
        return Problem.model_validate(
            self._request_object(
                "GET",
                f"/problems/{problem_id}",
                **request_options,
            )
        )

    def list_problems(self, *, page: int = 1, page_size: int = 10) -> ProblemList:
        """Return a page of platform problems."""

        page, page_size = self.validate_pagination(page, page_size)
        data = self._request_object(
            "GET", "/problems/", params={"page": page, "page_size": page_size}
        )
        return ProblemList.model_validate(data)

    def update_problem(self, problem_id: str, payload: Dict[str, Any]) -> Problem:
        """Update and return a platform problem."""

        problem_id = self._validate_path_identifier(problem_id, "problem_id")
        if not isinstance(payload, dict) or not payload:
            raise ValueError("problem update payload must be a non-empty dictionary")
        payload = dict(payload)
        for field in ("name", "method"):
            if field in payload:
                payload[field] = self.validate_identifier(payload[field], f"problem {field}")
        return Problem.model_validate(
            self._request_object("PUT", f"/problems/{problem_id}", json=payload)
        )

    def delete_problem(
        self,
        problem_id: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Delete a problem and return the platform confirmation."""

        problem_id = self._validate_path_identifier(problem_id, "problem_id")
        request_options = {} if timeout is None else {"timeout": timeout}
        return self._request_object(
            "DELETE",
            f"/problems/{problem_id}",
            **request_options,
        )

    def submit_job(
        self,
        problem_id: str,
        *,
        experiment_id: Optional[str] = None,
        experiment_name: Optional[str] = None,
        compute_resource_id: Optional[str] = None,
        method: Optional[str] = None,
        scheduled_at: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> JobSubmission:
        """Submit one existing problem and return its job acknowledgement."""

        problem_id = self._validate_path_identifier(problem_id, "problem_id")
        if experiment_id is not None:
            experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        if experiment_name is not None:
            experiment_name = self.validate_identifier(experiment_name, "experiment_name")
        if compute_resource_id is not None:
            compute_resource_id = self._validate_path_identifier(
                compute_resource_id,
                "compute_resource_id",
            )
        if method is not None:
            method = self.validate_identifier(method, "method")
        if scheduled_at is not None:
            scheduled_at = self.validate_scheduled_at(scheduled_at)
        selected_experiment = self._select_experiment(
            experiment_id,
            experiment_name,
            timeout=timeout,
        )
        payload: Dict[str, Any] = {
            "problem_id": problem_id,
            "experiment_id": selected_experiment,
        }
        if compute_resource_id:
            payload["compute_resource_id"] = compute_resource_id
        if method:
            payload["method"] = method
        if scheduled_at:
            payload.update({"is_scheduled": True, "scheduled_at": scheduled_at})
        request_options = {} if timeout is None else {"timeout": timeout}
        data = self._request_object(
            "POST",
            "/jobs/opt",
            json=payload,
            **request_options,
        )
        return JobSubmission.model_validate(data)

    def schedule_job(
        self,
        problem_id: str,
        scheduled_at: str,
        *,
        experiment_id: Optional[str] = None,
        experiment_name: Optional[str] = None,
        compute_resource_id: Optional[str] = None,
        method: Optional[str] = None,
    ) -> JobSubmission:
        """Schedule one existing problem at a timezone-aware timestamp."""

        problem_id = self._validate_path_identifier(problem_id, "problem_id")
        scheduled_at = self.validate_scheduled_at(scheduled_at)
        if experiment_id is not None:
            experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        if experiment_name is not None:
            experiment_name = self.validate_identifier(experiment_name, "experiment_name")
        if compute_resource_id is not None:
            compute_resource_id = self._validate_path_identifier(
                compute_resource_id,
                "compute_resource_id",
            )
        if method is not None:
            method = self.validate_identifier(method, "method")
        selected_experiment = self._select_experiment(experiment_id, experiment_name)
        payload = {
            "problem_id": problem_id,
            "experiment_id": selected_experiment,
            "scheduled_at": scheduled_at,
        }
        if compute_resource_id:
            payload["compute_resource_id"] = compute_resource_id
        if method:
            payload["method"] = method
        data = self._request_object("POST", "/jobs/opt/schedule", json=payload)
        return JobSubmission.model_validate(data)

    def get_job(self, job_id: str, *, timeout: Optional[float] = None) -> Job:
        """Return a platform job by ID."""

        job_id = self._validate_path_identifier(job_id, "job_id")
        request_options = {} if timeout is None else {"timeout": timeout}
        return Job.model_validate(self._request_object("GET", f"/jobs/{job_id}", **request_options))

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Request job cancellation and return the platform confirmation."""

        job_id = self._validate_path_identifier(job_id, "job_id")
        return self._request_object("DELETE", f"/jobs/{job_id}")

    def list_jobs(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
        period: Optional[str] = None,
        status: Optional[JobStatus | str] = None,
        compute_resource_id: Optional[str] = None,
    ) -> JobList:
        """Return a filtered page of the authenticated user's jobs."""

        params = self._validated_job_list_params(
            {
                key: value
                for key, value in {
                    "page": page,
                    "page_size": page_size,
                    "period": period,
                    "status": status,
                    "compute_resource_id": compute_resource_id,
                }.items()
                if value is not None
            }
        )
        return JobList.model_validate(self._request_object("GET", "/jobs/user", params=params))

    def list_problem_jobs(self, problem_id: str, **params: Any) -> JobList:
        """Return jobs created for one problem."""

        problem_id = self._validate_path_identifier(problem_id, "problem_id")
        params = self._validated_job_list_params(params)
        return JobList.model_validate(
            self._request_object("GET", f"/jobs/problem/{problem_id}", params=params)
        )

    def list_experiment_jobs(
        self,
        experiment_id: Optional[str] = None,
        *,
        experiment_name: Optional[str] = None,
        **params: Any,
    ) -> JobList:
        """Return jobs associated with one selected experiment."""

        if experiment_id is not None:
            experiment_id = self._validate_path_identifier(experiment_id, "experiment_id")
        if experiment_name is not None:
            experiment_name = self.validate_identifier(experiment_name, "experiment_name")
        params = self._validated_job_list_params(params)
        selected_experiment = self._select_experiment(experiment_id, experiment_name)
        return JobList.model_validate(
            self._request_object("GET", f"/jobs/experiment/{selected_experiment}", params=params)
        )

    def list_solvers(
        self, *, method: Optional[str] = None, active_only: bool = False
    ) -> List[ComputeResource]:
        """Return compute resources, optionally filtered by method and state."""

        if not isinstance(active_only, bool):
            raise ValueError("active_only must be a boolean")
        expected = (
            re.findall(
                r"[A-Z0-9]+",
                self.validate_identifier(method, "method").upper(),
            )
            if method is not None
            else None
        )
        if expected == []:
            raise ValueError("method must contain at least one letter or number")
        resources = [
            ComputeResource.model_validate(item)
            for item in self._request_array("GET", "/compute-resources/")
        ]
        if expected is not None:

            def matches(resource):
                source = resource.type or resource.backend_name
                words = re.findall(r"[A-Z0-9]+", (source or "").upper())
                return words[: len(expected)] == expected

            resources = [r for r in resources if matches(r)]
        if active_only:
            resources = [r for r in resources if (r.status or "").lower() == "active"]
        return resources

    def get_solver(self, resource_id: str) -> ComputeResource:
        """Return one compute resource by ID."""

        resource_id = self._validate_path_identifier(resource_id, "resource_id")
        return ComputeResource.model_validate(
            self._request_object("GET", f"/compute-resources/{resource_id}")
        )

    def upload_file(
        self,
        path: Path,
        *,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Upload a model file and return its platform storage record."""

        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Upload file not found: {path}")
        if user_id is not None:
            user_id = self._validate_path_identifier(user_id, "user_id")
        if organization_id is not None:
            organization_id = self._validate_path_identifier(organization_id, "organization_id")
        if workspace_id is not None:
            workspace_id = self._validate_path_identifier(workspace_id, "workspace_id")
        if project_id is not None:
            project_id = self._validate_path_identifier(project_id, "project_id")
        if not user_id or not organization_id:
            user = self.me(timeout=timeout)
            user_id = user_id or user.get("id") or user.get("ID") or user.get("user_id")
            organization_id = organization_id or user.get("organization_id")
        if not user_id or not organization_id:
            raise APIError("Current user response did not include user and organization IDs")
        user_id = self._validate_path_identifier(user_id, "user_id")
        organization_id = self._validate_path_identifier(organization_id, "organization_id")
        data = {"user_id": user_id, "organization_id": organization_id}
        if workspace_id:
            data["workspace_id"] = workspace_id
        if project_id:
            data["project_id"] = project_id
        with path.open("rb") as file_handle:
            request_options = {} if timeout is None else {"timeout": timeout}
            response = self._request(
                "POST",
                "/storage/upload",
                data=data,
                files={"file": (path.name, file_handle)},
                **request_options,
            )
        payload = self._response_object(response, "Storage upload")
        storage = payload.get("storage")
        if not isinstance(storage, dict):
            raise APIError("Storage upload response did not include a storage object")
        return storage

    def download_result(
        self,
        s3_path: str,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        """Download and decode a JSON result by its S3 path."""

        s3_path = self.validate_identifier(s3_path, "s3_path")
        request_options = {} if timeout is None else {"timeout": timeout}
        data = self._request_object(
            "POST",
            "/storage/download/by-s3-path",
            json={"s3_path": s3_path},
            **request_options,
        )
        return self._download_presigned_result(data, timeout=timeout)

    def download_result_by_id(
        self,
        storage_id: str,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        """Download and decode a JSON result by storage ID."""

        storage_id = self._validate_path_identifier(storage_id, "storage_id")
        request_options = {} if timeout is None else {"timeout": timeout}
        data = self._request_object(
            "GET",
            f"/storage/download/{storage_id}",
            **request_options,
        )
        return self._download_presigned_result(data, timeout=timeout)

    def delete_storage(
        self,
        storage_id: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Delete a storage record and return the platform confirmation."""

        storage_id = self._validate_path_identifier(storage_id, "storage_id")
        request_options = {} if timeout is None else {"timeout": timeout}
        return self._request_object(
            "DELETE",
            f"/storage/{storage_id}",
            **request_options,
        )

    def _download_presigned_result(
        self,
        data: Any,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        if not isinstance(data, dict) or not isinstance(data.get("presigned_url"), str):
            raise APIError("Storage download response did not include a presigned URL")
        request_timeout = (
            self.timeout if timeout is None else self.validate_timeout(timeout, "timeout")
        )
        response = self.session.get(data["presigned_url"], timeout=request_timeout)
        if response.status_code >= 400:
            self._raise_api_error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise APIError(
                "Stored solver result is not valid JSON",
                status_code=response.status_code,
            ) from exc

    def stream_job(
        self,
        job_id: str,
        *,
        connect_timeout: Optional[float] = None,
        read_timeout: float = 30.0,
    ) -> Iterator[SSEEvent]:
        """Yield decoded server-sent events for one job."""

        job_id = self._validate_path_identifier(job_id, "job_id")
        connect_timeout = self.validate_timeout(
            self.timeout if connect_timeout is None else connect_timeout,
            "connect_timeout",
        )
        read_timeout = self.validate_timeout(read_timeout, "read_timeout")
        response = self._request(
            "GET",
            f"/jobs/{job_id}/events",
            stream=True,
            timeout=(connect_timeout, read_timeout),
            headers={"Accept": "text/event-stream"},
        )
        event_name = ""
        data_lines: List[str] = []
        first_line = True

        def make_event():
            value = "\n".join(data_lines)
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                pass
            return SSEEvent(event=event_name or "message", data=value)

        try:
            for raw_line in response.iter_lines(decode_unicode=False):
                if isinstance(raw_line, bytes):
                    try:
                        line = raw_line.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise requests.exceptions.ContentDecodingError(
                            "Job event stream contained invalid UTF-8"
                        ) from exc
                elif isinstance(raw_line, str):
                    line = raw_line
                else:
                    raise requests.exceptions.ContentDecodingError(
                        "Job event stream returned a non-text line"
                    )
                if first_line:
                    line = line.removeprefix("\N{ZERO WIDTH NO-BREAK SPACE}")
                    first_line = False
                if line == "":
                    if data_lines:
                        yield make_event()
                    event_name, data_lines = "", []
                elif line.startswith(":"):
                    continue
                else:
                    field, separator, value = line.partition(":")
                    if separator and value.startswith(" "):
                        value = value[1:]
                    if field == "event":
                        event_name = value
                    elif field == "data":
                        data_lines.append(value)
        finally:
            response.close()

    def _poll_job(self, job_id, *, deadline, timeout, poll_interval):
        while time.monotonic() < deadline:
            remaining = max(1e-6, deadline - time.monotonic())
            try:
                return self.get_job(job_id, timeout=min(self.timeout, remaining))
            except (requests.RequestException, APIError) as exc:
                if isinstance(exc, APIError) and not self._is_transient_api_error(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s") from None
                time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: float = 5400.0,
        poll_interval: float = 5.0,
        use_sse: bool = True,
    ) -> Job:
        """Wait for completion through SSE with bounded polling fallback."""

        job_id = self._validate_path_identifier(job_id, "job_id")
        timeout = self.validate_timeout(timeout, "timeout")
        poll_interval = self.validate_timeout(poll_interval, "poll_interval")
        use_sse = self.validate_boolean(use_sse, "use_sse")
        deadline = time.monotonic() + timeout
        initial = self._poll_job(
            job_id, deadline=deadline, timeout=timeout, poll_interval=poll_interval
        )
        self._raise_unknown_status(initial)
        if initial.normalized_status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            return self._terminal_job(initial)
        if use_sse:
            for attempt in range(3):
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")
                    for event in self.stream_job(
                        job_id,
                        connect_timeout=min(self.timeout, remaining),
                        read_timeout=min(30.0, remaining),
                    ):
                        if isinstance(event.data, dict):
                            if "status" in event.data:
                                status = JobStatus.normalize(event.data["status"])
                                if status == JobStatus.UNKNOWN:
                                    raise APIError(
                                        f"Job {job_id} stream returned unknown status "
                                        f"{event.data['status']!r}"
                                    )
                                if status in {
                                    JobStatus.COMPLETED,
                                    JobStatus.FAILED,
                                    JobStatus.CANCELLED,
                                }:
                                    remaining = max(1e-6, deadline - time.monotonic())
                                    return self._terminal_job(
                                        self.get_job(
                                            job_id,
                                            timeout=min(self.timeout, remaining),
                                        )
                                    )
                        if event.event == "done":
                            remaining = max(1e-6, deadline - time.monotonic())
                            return self._terminal_job(
                                self.get_job(
                                    job_id,
                                    timeout=min(self.timeout, remaining),
                                )
                            )
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")
                except (requests.RequestException, APIError) as exc:
                    if isinstance(exc, APIError) and not self._is_transient_api_error(exc):
                        raise
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(min(2**attempt, max(0.0, deadline - time.monotonic())))
        while time.monotonic() < deadline:
            job = self._poll_job(
                job_id, deadline=deadline, timeout=timeout, poll_interval=poll_interval
            )
            self._raise_unknown_status(job)
            if job.normalized_status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                return self._terminal_job(job)
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

    @staticmethod
    def _terminal_job(job: Job) -> Job:
        if job.normalized_status == JobStatus.FAILED:
            raise JobFailedError(job.id, job.error or "unknown backend error")
        if job.normalized_status == JobStatus.CANCELLED:
            raise JobCancelledError(job.id, job.cancellation_reason or job.error or "cancelled")
        if job.normalized_status == JobStatus.COMPLETED:
            return job
        raise APIError(f"Job {job.id} emitted a terminal event but status is {job.status!r}")

    @staticmethod
    def _raise_unknown_status(job: Job) -> None:
        if job.normalized_status == JobStatus.UNKNOWN:
            raise APIError(f"Job {job.id} returned unknown status {job.status!r}")

    @staticmethod
    def _is_transient_api_error(error: APIError) -> bool:
        return error.status_code in {408, 425, 429, 500, 502, 503, 504}

    def extract_result(self, job: Job, *, timeout: Optional[float] = None) -> Any:
        """Extract inline or storage-backed data from a Job without checking its state."""

        if not isinstance(job, Job):
            raise TypeError("job must be a platform Job")
        value = job.server_response
        if value is None and job.server_response_id:
            value = self.download_result_by_id(job.server_response_id, timeout=timeout)
        if isinstance(value, str):
            if value.startswith("s3://"):
                value = self.download_result(value, timeout=timeout)
            else:
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
        if isinstance(value, dict):
            for key in ("result", "results"):
                if key in value and isinstance(value[key], (dict, list)):
                    return value[key]
        return value

    def health(self) -> bool:
        """Return whether the authenticated user endpoint is reachable."""

        try:
            self._request("GET", "/users/me", timeout=5.0)
            return True
        except (requests.RequestException, APIError):
            return False
