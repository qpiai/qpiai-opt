# Platform client

`qpiai_opt.opt_solvers.Client` is the authenticated interface to QpiAI Opt Platform records and job execution. Use it for discovery, scheduling, monitoring, storage, or administrative problem and job operations.

Direct solver constructors accept either their normal `api_key` arguments or an initialized `Client` instance. Arbitrary client-like objects are not part of the public contract.

## Construction

Register for a free Opt API key at [QpiAI Opt](https://opt.qpiai.tech/), then supply it to `Client` from your application's secret store.

```python
import os

from qpiai_opt.opt_solvers import Client

client = Client(
    api_key=os.environ["QPIAI_OPT_API_KEY"],
    experiment_name="Default Experiment",
    base_url="https://opt-server.qpiai.tech/api",
    timeout=60,
)
```

| Argument | Meaning |
| --- | --- |
| `api_key` | Required non-empty API key. Regular requests send it as `X-Secret-Token`. |
| `experiment_id` | Optional client-wide experiment ID. It takes precedence over a name. |
| `experiment_name` | Client-wide exact experiment name. Defaults to `Default Experiment`. |
| `base_url` | HTTP(S) platform API root. Queries and fragments are rejected. |
| `timeout` | Default positive finite timeout for each HTTP request, in seconds. |
| `session` | Optional `requests.Session` for custom connection pooling or transport configuration. |

When a caller supplies a `requests.Session`, the caller owns its lifecycle. The current `Client` has no context-manager or `close()` method and does not close a supplied session. A client-created session is retained for the `Client` object's lifetime.

Construction validates local configuration without contacting the platform. API keys are stripped and must be nonempty; this check does not prove that a key is accepted. Entity IDs used in URL paths are stripped strings containing only ASCII letters, digits, `.`, `_`, `-`, or `~`, and cannot be `.` or `..`. Names, roles, email selectors, and search values use the nonempty-string rule rather than this path-ID grammar. The server enforces role, membership, and account permissions.

## Authentication and account operations

```python
verification = client.verify_api_key()
user = client.me()
token_record = client.get_api_key()
```

| Method | Return |
| --- | --- |
| `verify_api_key(api_key=None)` | Raw verification dictionary from the platform. The optional key is verified without changing `client.api_key`. |
| `me(timeout=None)` | Raw authenticated-user dictionary. |
| `get_api_key()` | Raw access-token record. |
| `regenerate_api_key()` | Raw new-token record. |
| `health()` | Checks authenticated `/users/me` with a five-second request timeout; returns `True` for a successful HTTP status or `False` on request/API failure. |

Token-returning dictionaries can contain secret material. Do not log, commit, or attach them to exception telemetry.

Methods returning JSON records validate the response's required object or array shape. Object endpoints reject arrays and scalars, list endpoints reject objects, and invalid JSON raises `APIError` with the HTTP status when available. Nested required objects, such as an uploaded storage record, also fail closed when missing.

## Organizations and collaboration

The secret-token middleware authorizes normal collaboration routes. Root-only global listing and organization creation are not exposed.

### Organization and invitations

| Method | Return |
|---|---|
| `get_organization(organization_id)` | `Organization`, including `.users` when supplied |
| `update_organization(organization_id, name=...)`, `delete_organization(organization_id)` | confirmation dictionary; server role checks apply |
| `invite_member(organization_id, email=..., role=...)` | invitation result dictionary |
| `invite_members(organization_id, users)` | bulk result; nonempty list of `{email, role}` |
| `list_invitations(organization_id)` | `list[Invitation]` |
| `cancel_invitation(invitation_id)` | confirmation dictionary |
| `update_member_role(organization_id, user_id, role=...)` | confirmation dictionary |
| `can_invite_members(organization_id)` | seat/permission dictionary |
| `get_organization_statistics(organization_id)` | statistics dictionary |
| `get_organization_compute_resources(organization_id)` | `list[ComputeResource]` |

Identifiers, email, and role must be nonempty strings. Valid roles and authorization remain server policy.

Bulk invitations require an actual nonempty list of dictionaries, each containing exactly `email` and `role`. Unknown fields are rejected. All entries are validated before the invitation request. A successful response is the server's acknowledgement dictionary; the client does not infer a per-member outcome beyond that payload.

### Workspaces

| Method | Return / behavior |
|---|---|
| `create_workspace(organization_id, name=..., description="")` | `Workspace`; the authenticated user is sent as the required owner |
| `get_workspace`, `update_workspace`, `delete_workspace` | `Workspace`, `Workspace`, confirmation dictionary |
| `list_workspaces(organization_id, page=1, page_size=10)` | Pagination response with `.workspaces` and `.pagination` |
| `list_my_workspaces(page=1, page_size=10, search=None)` | Pagination response with `.workspaces`, `.pagination`, and optional `.summary` |
| `list_workspace_members(id)` | `list[UserSummary]` |
| `add_workspace_member(id, user_id=... \| email=...)` | exactly one selector is required |
| `add_workspace_members(id, emails)` | bulk result for a nonempty email list |
| `remove_workspace_member(id, user_id)` | confirmation dictionary |

Bulk workspace membership accepts an actual nonempty `list[str]` of nonempty stripped email selectors. Single-member workspace and project calls require exactly one of `user_id` or `email`; supplying both, neither, or an empty supplied value fails validation.

### Projects and experiments

| Method | Return / behavior |
|---|---|
| `create_project(workspace_id, name=..., description="")` | `Project`; server assigns authenticated owner |
| `get_project`, `update_project`, `delete_project` | `Project`, `Project`, confirmation dictionary |
| `list_projects(workspace_id, page=1, page_size=10, search=None)` | Pagination response with `.projects` and `.pagination` |
| `list_my_projects(page=1, page_size=10)` | Pagination response with `.projects` and `.pagination` |
| `list_project_members`, `add_project_member`, `remove_project_member` | member list / confirmation; add accepts exactly one of user ID or email |
| `create_experiment(project_id, name=..., description="")` | `Experiment`; the authenticated user is sent as the required owner |
| `get_experiment`, `update_experiment`, `delete_experiment` | `Experiment`, `Experiment`, confirmation dictionary |
| `list_experiments(project_id, page=1, page_size=10, search=None)` | Pagination response with `.experiments` and `.pagination` |
| `list_my_experiments(page=1, page_size=10)` | Pagination response with `.experiments` and `.pagination` |
| `list_workspace_experiments(workspace_id, page=1, page_size=10)` | Pagination response with `.experiments` and `.pagination` |
| `list_experiment_members`, `add_experiment_member`, `remove_experiment_member` | member list / confirmation; experiment add uses user ID |
| `associate_compute_resource(experiment_id, compute_resource_id)` | confirmation dictionary |

Pagination values are strict positive integers. Optional search strings must be nonempty when provided. Entity identifiers are exposed as `id`.

Create/update `description` accepts any string, including the default `""`. Updates to workspaces, projects, and experiments send both `name` and `description`; omitting `description` therefore requests the empty string rather than retaining the previous description. These updates fetch the current typed record first, merge the returned update payload, and return the validated record. The merge does not make the operation atomic against concurrent server changes.

Collection records use `None` for optional data that a list operation does not supply. In particular, membership summaries can omit `status` and `organization_id`, and experiments returned by `list_my_experiments()` can omit `workspace_id`. Supplied text remains validated as nonempty.

## Experiment selection

Every submitted job requires an experiment ID. Selection order is:

1. `experiment_id` passed to the operation;
2. `experiment_name` passed to the operation;
3. client-wide `experiment_id`;
4. client-wide `experiment_name`;
5. exact `Default Experiment`.

```python
experiment_id = client.resolve_experiment("Benchmarks")
```

`resolve_experiment()` requests an exact name within the authenticated organization. Successful resolutions are cached by name on the client. A missing `experiment_id` in the lookup response raises `APIError`.

## Problems

```python
problem = client.create_problem(
    {
        "name": "External problem",
        "method": "QUBO",
        "qubo_dict": {"0,0": -1.0},
        "num_variables": 1,
    }
)

same_problem = client.get_problem(problem.id)
page = client.list_problems(page=1, page_size=20)
updated = client.update_problem(problem.id, {"name": "Renamed problem"})
confirmation = client.delete_problem(problem.id)
```

`create_problem()` requires a dictionary with non-empty `name` and `method`. Method-specific payload fields are passed to the platform. High-level solvers should be preferred for QUBO/HUBO/MILP creation because they validate and serialize those fields.

Returns:

- `create_problem()`, `get_problem()`, and `update_problem()` return `Problem`;
- `list_problems()` returns a pagination response containing `.problems` and `.pagination`;
- `delete_problem()` returns the platform confirmation dictionary.

Page and page size are strict positive integers.

## Advanced solver discovery and resource pinning

```python
resources = client.list_solvers(method="QUBO", active_only=True)
resource = client.get_solver(resources[0].id)
```

`list_solvers()` returns `list[ComputeResource]`. `method` filters by problem class, such as `"QUBO"`, `"HUBO"`, or `"MILP"`.

`active_only=True` retains resources whose status is `"active"` ignoring case. Resource availability and allocation can change between discovery and submission; a discovered record is not a reservation.

Most applications should skip this section and omit `compute_resource_id`. The platform selects an eligible resource from the submitted problem method. A compute-resource ID is an opaque platform UUID for a particular solver deployment; it is useful only when an advanced workflow must pin that specific deployment.

## Job submission and scheduling

High-level solvers package the class-specific payload. Direct submission is available when a problem already exists:

```python
submission = client.submit_job(
    problem.id,
    method="QUBO",
    experiment_name="Benchmarks",
)

pinned_submission = client.submit_job(
    problem.id,
    method="QUBO",
    experiment_name="Benchmarks",
    compute_resource_id=resource.id,
)

scheduled = client.schedule_job(
    problem.id,
    "2030-01-01T09:30:00+05:30",
    method="QUBO",
)
```

`scheduled_at` must be an ISO 8601 timestamp with an explicit timezone. Direct `submit_job(..., scheduled_at=...)` sets the platform scheduling fields; `schedule_job()` uses the dedicated scheduling endpoint.

Both methods return an acknowledgement with `.message`, optional `.expected_run_at`, and the new `.job`:

- `.message`: acknowledgement text;
- `.expected_run_at`: optional `datetime` parsed from the platform response;
- `.job`: the created `Job`.

Submission is nonblocking; the response contains the job state supplied by the platform.

`pinned_submission` places the discovered resource ID in the job request. Use an ID returned for the same `method`; omit it to let the platform choose.

## Reading and filtering jobs

```python
job = client.get_job(submission.job.id)

user_jobs = client.list_jobs(
    page=1,
    page_size=20,
    status="running",
    compute_resource_id=resource.id,
)
problem_jobs = client.list_problem_jobs(problem.id, page=1, page_size=20)
experiment_jobs = client.list_experiment_jobs(
    experiment_name="Benchmarks",
    page=1,
    page_size=20,
)
```

`get_job()` returns `Job`. List methods return a pagination response containing `.jobs`, `.pagination`, and optional aggregate execution/credit totals. `status` accepts `JobStatus.PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, or `CANCELLED`, or the corresponding lowercase strings. Surrounding whitespace is ignored; uppercase strings, `"unknown"`, and other values are rejected. `period` and `compute_resource_id` must be nonempty strings when supplied.

Job-list `period` filters use job creation time: `daily` starts at the current UTC day's boundary, `weekly` starts seven days before the request, and `monthly` starts one calendar month before the request. Omit `period` for all dates. The SDK strips whitespace but does not validate these choices; other strings apply no time filter. `compute_resource_id` filters `list_jobs()` and `list_experiment_jobs()`. The problem-job endpoint does not apply this filter, although `list_problem_jobs()` accepts the argument.

## Credit usage and allocations

Platform usage charges are based on compute time (`compute_time_s`), not solver time (`solver_time_s`). Credit usage reports contain the service-recorded charges.

Credit information is read-only account information. A completed or retrieved `Job` exposes `credits_used` and `credits_reserved` when the platform supplies them. Period reports and current allocations are available through the same authenticated client:

```python
job = client.get_job(submission.job.id)
job_credits = job.credits_used

usage = client.get_user_credit_usage(period="monthly")
usage_summary = (usage.used_credits, len(usage.jobs))

quota_balances = {
    quota.compute_resource_id: quota.remaining_credits
    for quota in client.get_user_quotas()
}
```

The methods are:

| Method | Return | Scope |
|---|---|---|
| `get_user_credit_usage(user_id=None, period="monthly", by_resource=False)` | `CreditUsage` or `CreditUsageByResource` | One user's usage |
| `get_workspace_credit_usage(workspace_id, period="monthly", by_resource=False)` | `CreditUsage` or `CreditUsageByResource` | A workspace the caller may access |
| `get_organization_credit_usage(organization_id=None, period="monthly", by_resource=False)` | `CreditUsage` or `CreditUsageByResource` | An organization the caller may access |
| `get_user_quotas(user_id=None)` | `list[CreditQuota]` | Per-resource user allocations |
| `get_workspace_quotas(workspace_id)` | `list[CreditQuota]` | Per-resource workspace allocations |
| `get_organization_quotas(organization_id=None)` | `list[CreditQuota]` | Per-resource organization allocations |

`period` accepts `daily`, `weekly`, `monthly`, or `yearly`. Set `by_resource=True` to receive one `CreditResourceUsage` record per compute resource; the SDK checks that those records add up to the report total. `CreditUsage.jobs` contains typed job identifiers, statuses, resource names, execution times, and credits used when present.

For a quota, `capacity_credits` is the platform's enforceable allocation (`total_credits` for an organization quota, otherwise `allocated_credits`) and `remaining_credits` is that capacity minus `used_credits`. `remaining_credits` reports the balance before reservations. The platform also accounts for `reserved_credits` when deciding whether a new job can run. A quota with inconsistent values is rejected rather than presented as a misleading balance. Access to another user, workspace, or organization is still controlled by the platform; an authorization error is returned as `AuthenticationError` or `APIError`.

Cancel a job with:

```python
confirmation = client.cancel_job(job.id)
```

This returns the platform dictionary. Cancellation is a request; read the job again or wait for a terminal state to confirm the final result.

## Server-Sent Events and waiting

```python
for event in client.stream_job(
    job.id,
    connect_timeout=10,
    read_timeout=30,
):
    event_record = (event.event, event.data)
```

`stream_job()` yields `SSEEvent`. Multi-line `data:` fields are joined with newlines. JSON data is decoded; other content remains a string. Its response is closed when the generator is exhausted, explicitly closed, or raises. Close the generator when stopping iteration early. Each `event_record` contains the event name and its decoded JSON value or text; JSON scalars and `null` are valid payloads.

The stream is decoded as UTF-8. A leading UTF-8 byte-order mark is ignored, comment lines beginning with `:` and unknown fields are ignored, and an omitted or empty `event` field becomes `"message"`. An event is dispatched only at a blank-line boundary and only when at least one `data` field was received. Invalid UTF-8 raises `requests.exceptions.ContentDecodingError`.

The common waiter uses SSE first:

```python
completed_job = client.wait_for_job(
    job.id,
    timeout=3600,
    poll_interval=5,
    use_sse=True,
)
```

Behavior:

- reads the current job before opening the stream;
- makes at most three SSE connection attempts, with bounded exponential backoff after transient failures;
- falls back to polling;
- never schedules a sleep beyond the total `timeout`;
- returns a completed `Job`;
- raises `JobFailedError` or `JobCancelledError` for those terminal states;
- raises `TimeoutError` when the monitoring budget is exhausted;
- raises `APIError` for an unknown platform state.

Set `use_sse=False` for polling only.

Transient HTTP statuses are `408`, `425`, `429`, `500`, `502`, `503`, and `504`; other API errors propagate. Request exceptions can also trigger retry or polling fallback. Monitoring retries do not submit another job. See the [timeout contract](#timeouts) for in-flight request behaviour at the budget boundary.

## Storage and result extraction

```python
from pathlib import Path

storage = client.upload_file(
    Path("model.lp"),
    workspace_id="workspace-id",
    project_id="project-id",
    timeout=120,
)
```

`path` must identify an existing regular file; a missing file raises `FileNotFoundError` before upload. If `user_id` or `organization_id` is omitted, `upload_file()` obtains them from `me()`. It uploads the bytes with the basename and the supplied ownership fields. The file handle is closed after the request.

The return is the platform's nested `storage` dictionary. The client validates that this is a dictionary, but does not validate individual storage fields. The platform's storage identifier is conventionally `ID`; validate that value before passing it to another operation. High-level MILP submission performs its own storage-ID validation.

Download a JSON object by storage ID or S3 path:

```python
by_id = client.download_result_by_id(storage["ID"], timeout=60)
by_path = client.download_result("s3://bucket/key", timeout=60)
```

The timeout applies separately to the platform request that obtains the presigned URL and the subsequent object-storage GET. Non-JSON object content raises `APIError`.

`extract_result(job)` handles all supported result locations:

1. inline `job.server_response`;
2. `job.server_response_id`;
3. an inline `s3://...` string;
4. JSON encoded as a string.

If the decoded value has a dictionary/list under `result` or `results`, that inner value is returned. Otherwise the decoded value is returned unchanged. Class-specific solvers require a completed job to contain a valid result object. A completed lifecycle state with no retrievable result raises instead of producing a partial solve result.

Delete storage explicitly:

```python
confirmation = client.delete_storage(storage["ID"])
```

## Timeouts

There are two distinct timeout concepts.

Individual request timeouts configure connection and read waits:

- `Client(timeout=...)` sets the default;
- method-level `timeout=...` overrides it where exposed;
- solver `request_timeout` is passed through create, submit, upload, and result requests.

A scalar timeout applies to both connection establishment and periods waiting for response data. It is not a total download deadline; a response that continues sending data can take longer. This follows the [Requests timeout contract](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts).

Job wait timeouts set a monitoring budget:

- `Client.wait_for_job(timeout=...)`;
- `QUBOSolver.solve(timeout=...)`;
- `HUBOSolver.solve(timeout=...)`;
- `MILPSolver.solve(timeout=...)`.

The waiter checks its monotonic budget between operations and bounds requested sleeps and request timeouts by the remaining time. An in-flight request can still finish after that budget, so the value is not a strict wall-clock cancellation deadline. It does not cancel the platform job.

A request timeout raises `requests.Timeout`; a streamed read can instead surface a Requests connection error. Budget exhaustion in the job waiter raises the built-in `TimeoutError`.

## Failure and retry policy

Do not automatically retry a failed submission without determining whether the platform accepted it. A transport failure or HTTP 408, 425, 429, or server error can occur after record creation.

High-level solver behavior:

- definitive non-transient 4xx rejection triggers best-effort cleanup of SDK-created problem records;
- MILP also attempts cleanup of SDK-owned uploaded storage after definitive rejection;
- ambiguous outcomes retain records and add reconciliation instructions to the exception;
- the SDK never resubmits a paid optimization job inside its wait/reconnect logic.

Use the problem ID from the exception note or submission response:

```python
jobs = client.list_problem_jobs(problem_id, page=1, page_size=20)
```

Solver-specific response fields are documented in the QUBO, HUBO, and MILP guides. Common results and failures are in [Local solving](local-solving.md) and [Automation](../automation.md).

## Response models

Typed platform records are Pydantic models. Timestamps are timezone-aware when the platform includes an offset. The fields below are the supported contract. Serialize a record with `model_dump(mode="json")`:

```python
job = client.get_job("job-123")
payload = job.model_dump(mode="json")
```

### Jobs and problems

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `status` | `str` | Platform identifier and raw lifecycle state. |
| `method`, `problem_id`, `experiment_id` | `str \| None` | Associated problem metadata. |
| `server_response`, `server_response_id` | any / `str \| None` | Inline result or storage reference. |
| `error` | `str \| None` | Platform failure detail. |
| `compute_time_s`, `solver_time_s` | `float \| None` | Nonnegative compute and solver durations in seconds when supplied; service timing aliases are normalized to these names. |
| `created_at`, `updated_at`, `cancelled_at` | datetime or `None` | Platform timestamps. |
| `cancellation_reason` | `str \| None` | Cancellation detail when supplied. |
| `credits_used`, `credits_reserved` | `int \| None` | Non-negative charged and reserved credits when supplied. |

`job.normalized_status` maps the raw `status` to `JobStatus.PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, or `UNKNOWN`. It does not modify the raw field.

| Returned object | Fields |
| --- | --- |
| `Problem` | `id: str`, `name: str`, `method: str`, `created_at: datetime \| None`, `updated_at: datetime \| None` |
| Pagination record inside a list response | `current_page: int = 1`, `page_size: int = 10`, `total: int = 0`; all are non-negative and page values start at one |
| `list_problems()` response | `problems: list[Problem]` and a `pagination` record |
| `submit_job()` or `schedule_job()` response | `message: str`, `expected_run_at: datetime \| None`, `job: Job` |
| Job-list response | `jobs: list[Job]`, a `pagination` record, `total_compute_time_s: float \| None`, and `total_credits_used: float \| None` |

### Compute resources and credit records

| Model | Field | Type | Meaning |
| --- | --- | --- | --- |
| `ComputeResource` | `id` | `str` | Opaque deployment identifier. |
|  | `backend_name`, `type`, `device_name`, `status` | `str \| None` | Backend, solver class, device label, and availability state. |
|  | `usage_rate` | `float \| None` | Non-negative platform-supplied usage rate. |
| `CreditUsageJob` | `job_id`, `credits_used` | `str`, `int` | Job identifier and non-negative charged credits. |
|  | `name`, `user_name`, `device_name`, `method`, `status` | `str \| None` | Display and execution metadata. |
|  | `compute_resource_id`, `compute_backend` | `str \| None` | Resource identifier and backend. |
|  | `executed_at` | `datetime \| None` | Execution timestamp. |
| `CreditUsage` | `used_credits`, `start_date`, `end_date` | `int`, `datetime`, `datetime` | Total and platform-supplied report period; the end cannot precede the start. |
|  | `jobs` | `list[CreditUsageJob]` | Jobs included in the report. |
| `CreditResourceUsage` | `compute_resource_id`, `used_credits`, `job_count` | `str`, `int`, `int` | Resource identifier, charge total, and job count. |
|  | `compute_backend` | `str \| None` | Backend label. |
| `CreditUsageByResource` | `used_credits`, `start_date`, `end_date` | `int`, `datetime`, `datetime` | Total and report period. |
|  | `compute_resource_usages` | `list[CreditResourceUsage]` | Per-resource records; their credits must sum to `used_credits`. |

`ComputeResource` accepts the public field name `id` and the platform payload alias `ID`; serialization uses `id`.

`CreditQuota` fields are:

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `compute_resource_id` | `str` | Quota and resource identifiers. |
| `allocated_credits` | `int` | Base allocation. |
| `used_credits`, `reserved_credits` | `int = 0` | Charged and separately reported reserved amounts. |
| `total_credits` | `int \| None` | Enforced organization capacity when present; otherwise `allocated_credits` is the capacity. |
| `top_up_credits` | `int = 0` | Additional credits reported by the platform. |
| `top_up_expiry_date` | `datetime \| None` | Top-up expiry. |
| `from_date`, `to_date` | `datetime \| None` | Allocation period; the end cannot precede the start. |
| `user_id`, `workspace_id`, `organization_id` | `str \| None` | Owning scope. |
| `compute_resource` | `dict \| None` | Embedded resource payload when supplied. |
| `capacity_credits` | read-only `int` | `total_credits` when present, otherwise `allocated_credits`. |
| `remaining_credits` | read-only `int` | `capacity_credits - used_credits`. |

All credit counts reject Booleans, negative values, and non-integers. `used_credits` cannot exceed quota capacity.

### Collaboration records

| Returned object | Fields |
| --- | --- |
| `UserSummary` | `id: str`; optional `name`, `email`, `role`, `status`, `organization_id`; `is_revoked: bool \| None` |
| `Organization` | `id: str`, `organization_name: str`, optional `billing_details`; optional non-negative `min_booking_minutes`, `max_booking_minutes`, `max_daily_minutes`, `current_seat_count`; `users: list[UserSummary]`; optional `created_at`, `updated_at` |
| `Workspace` | `id: str`, `owner_id: str \| None`, `name: str`, `description: str`, `organization_id: str`, `is_default: bool`, optional `created_at`, `updated_at` |
| `Project` | `id: str`, `owner_id: str`, `name: str`, `description: str`, `workspace_id: str`, `is_default: bool`, optional `created_at`, `updated_at` |
| `Experiment` | `id: str`, `owner_id: str`, `name: str`, `description: str`, `project_id: str`, optional `workspace_id`, `compute_resource_id`; `is_default: bool`; non-negative `total_compute_time_s: float`, `total_credits_used: int`; optional `created_at`, `updated_at` |
| `Invitation` | `id: str`, `organization_id: str`, `email: str`, `role: str`, `status: str`; optional `invitation_type`, `invited_by`, `expires_at`, `created_at`, `updated_at` |
| Workspace-list response | `workspaces: list[Workspace]`, a `pagination` record, and optional `summary` |
| Project-list response | `projects: list[Project]` and a `pagination` record |
| Experiment-list response | `experiments: list[Experiment]` and a `pagination` record |

`Organization.min_booking_minutes` cannot exceed `max_booking_minutes`. Text identifiers are stripped and must be non-empty when present.

### Server-sent events

`SSEEvent` contains `event: str` and decoded `data: Any`. The event name is non-empty. JSON data becomes its Python value; other data remains text.

## Error reference

Confirmation endpoints return the platform acknowledgement dictionary. Its keys depend on the endpoint; treat a successful return as confirmation and use the typed retrieval operation when current entity state is required.

Common failures are:

| Exception/message | Cause | Action |
| --- | --- | --- |
| `ValueError: API key is missing / invalid. Get a valid API key by registering at https://opt.qpiai.tech/` | Missing or empty credential at client construction. | Load a key from a secret manager and keep it outside logs. |
| `AuthenticationError` | The platform rejected the credential. | Replace/revoke the key; do not retry the same key indefinitely. |
| `APIError: ... returned invalid JSON` | An object/array endpoint returned a non-JSON body. | Preserve the status and response context and report the platform failure. |
| `APIError: ... expected a JSON object` / `... array` | The response shape contradicts the endpoint contract. | Treat the response as invalid; do not guess a wrapper. |
| `requests.Timeout` | One HTTP exchange exceeded its request timeout. | Reconcile a submission before retrying; retry read-only calls within an application deadline. |
| `TimeoutError` | A job did not reach a terminal state before the total wait deadline. | Keep the same job ID and resume monitoring. |
| `JobFailedError` | The job reached a failed terminal state. | Read `job.error`; there is no completed solution. |
| `JobCancelledError` | The job reached a cancelled terminal state. | Treat it as no solution. |
| `RuntimeError: <class> job <id> returned an invalid result` | Lifecycle state is completed but no class-specific result can be retrieved/validated. | Preserve the job/problem IDs and investigate the existing record. |

For `APIError`, `status_code` is available when the failure came from an HTTP response. Validation errors occur before a network side effect whenever the SDK has all required information locally.
