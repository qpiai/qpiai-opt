# Common solver API reference

Import routing, local execution, platform records, and automation operations from `qpiai_opt.opt_solvers`. Solver-specific classes use their problem-class subpackages.

```python
from qpiai_opt.opt_solvers import Client, classify, inspect, solve
```

## Module: `milp`

Solver-specific namespace `qpiai_opt.opt_solvers.milp`. See the [MILP solver API](api-milp.md), [LP/MILP modelling guide](milp-modeller.md), and [QP modelling guide](qp-modeller.md).

## Module: `qubo`

Solver-specific namespace `qpiai_opt.opt_solvers.qubo`. See the [QUBO solver API](api-qubo.md) and [QUBO modelling guide](qubo-modeller.md).

## Module: `hubo`

Solver-specific namespace `qpiai_opt.opt_solvers.hubo`. See the [HUBO solver API](api-hubo.md) and [HUBO modelling guide](hubo-modeller.md).

## Module: `ising`

Native spin-problem namespace `qpiai_opt.opt_solvers.ising`. It exports `IsingProblem`; see its constructor and methods below and the [Ising workflow](ising-modeller.md).

## Class: `Client`

```text
Client(*, api_key: 'Optional[str]' = None, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, base_url: 'str' = 'https://opt-server.qpiai.tech/api', timeout: 'float' = 60.0, session: 'Optional[requests.Session]' = None) -> 'None'
```

Authenticated client for QpiAI Platform resources, jobs, and storage.

Public class attributes: `DEFAULT_URL = "https://opt-server.qpiai.tech/api"` and `DEFAULT_EXPERIMENT_NAME = "Default Experiment"`.

Construction is keyword-only and performs no HTTP request. `api_key` must be a nonempty string despite its `None` signature default; it is stripped and stored. The SDK does not load a key from an environment variable automatically. `base_url`, optional experiment selectors, and the positive finite non-Boolean `timeout` are validated locally. `experiment_name=None` selects the default name. `session` is an optional `requests.Session`; a supplied session remains caller-owned. See [construction and transport](platform-client.md#construction).

### Shared argument and failure rules

These rules apply to the client methods below unless a method states a narrower contract:

- IDs inserted into URL paths are nonempty stripped strings using only ASCII letters, digits, `.`, `_`, `-`, and `~`; `.` and `..` are rejected. Other names, role strings, email selectors, and search strings must be nonempty after stripping. Email syntax and allowed roles remain server policy.
- `description` accepts any string, including `""`; it is not stripped. Pagination takes positive non-Boolean integral values and normalizes them to Python integers. Boolean options require Python `bool` values.
- Optional request `timeout=None` uses the client default; a supplied scalar must be finite and positive. Request timeouts control connection/read waits, not total elapsed download time. Job monitoring has a separate budget; see [timeouts](platform-client.md#timeouts).
- Invalid SDK arguments raise `ValueError`. HTTP `401` raises `AuthenticationError`; other HTTP error responses raise `APIError`. Transport exceptions from Requests propagate. Invalid JSON or the wrong top-level object/array shape raises `APIError`; malformed typed records raise Pydantic validation errors. Server authorization applies to every operation.
- Methods returning `dict[str, Any]` preserve the endpoint's acknowledgement or raw record; keys are not a uniform schema. Typed returns use the [platform record contracts](#platform-record-conventions). The client has no automatic write retry or server-side idempotency guarantee.

All methods below use the configured platform transport. Local-solver authentication is a separate runtime operation and does not inherit a custom client `base_url` or session.

### Methods and properties

#### Method: `validate_identifier`

```text
validate_identifier(value: 'str', name: 'str') -> 'str'
```

Return `value` stripped of surrounding whitespace. `value` must be a non-empty string; `name` identifies the argument in the `ValueError` message.

#### Method: `validate_base_url`

```text
validate_base_url(value: 'str') -> 'str'
```

Return an HTTP(S) base URL without trailing slashes. Empty values, URLs without a network location, other schemes, queries, and fragments raise `ValueError`.

#### Method: `validate_pagination`

```text
validate_pagination(page: 'int', page_size: 'int') -> 'tuple[int, int]'
```

Return `(page, page_size)` as Python integers. Both inputs must be positive integral values; booleans are rejected.

#### Method: `validate_boolean`

```text
validate_boolean(value: 'bool', name: 'str') -> 'bool'
```

Return a strict Boolean input. Other values raise `ValueError` using `name` to identify the argument.

#### Method: `validate_scheduled_at`

```text
validate_scheduled_at(value: 'str') -> 'str'
```

Return the stripped ISO 8601 timestamp string after verifying that it contains a timezone offset. A trailing `Z` is accepted; empty, malformed, or timezone-naive values raise `ValueError`.

#### Method: `validate_timeout`

```text
validate_timeout(value: 'float', name: 'str' = 'timeout') -> 'float'
```

Return a positive finite timeout as `float`. Booleans, non-real values, zero, negative values, and non-finite values raise `ValueError`.

#### Property: `headers`

```python
headers
```

Return a new `dict[str, str]` containing `{"X-Secret-Token": client.api_key}`. Treat the returned value as secret material; modifying it does not change the client.

#### Method: `verify_api_key`

```text
verify_api_key(api_key: 'Optional[str]' = None) -> 'Dict[str, Any]'
```

Verify the supplied nonempty stripped key, or `self.api_key` when omitted, and return the raw verification `dict`. This read does not replace the stored credential. The request uses `self.timeout`; malformed or rejected responses follow the shared error rules.

#### Method: `me`

```text
me(*, timeout: 'Optional[float]' = None) -> 'Dict[str, Any]'
```

Return the authenticated user's raw `dict`. `timeout` optionally overrides the request timeout; no fixed user-field schema is imposed by this method.

#### Method: `get_api_key`

```text
get_api_key() -> 'Dict[str, Any]'
```

Return the current raw access-token `dict`; it can contain secret material. No local credential value is changed.

#### Method: `regenerate_api_key`

```text
regenerate_api_key() -> 'Dict[str, Any]'
```

Request token regeneration and return its raw `dict`. This changes server state but does not automatically replace `client.api_key`; read the returned record through trusted application code before making further authenticated calls. Do not log the record.

#### Method: `get_user_credit_usage`

```text
get_user_credit_usage(user_id: 'Optional[str]' = None, *, period: 'str' = 'monthly', by_resource: 'bool' = False, timeout: 'Optional[float]' = None) -> 'Union[CreditUsage, CreditUsageByResource]'
```

Return `CreditUsage` by default or `CreditUsageByResource` for `by_resource=True`. Omitted `user_id` is resolved through `me()`. `period` is stripped/lowercased and must be `daily`, `weekly`, `monthly`, or `yearly`; `by_resource` must be Boolean. `timeout` applies to each required request. A current-user record without an ID raises `APIError`.

#### Method: `get_workspace_credit_usage`

```text
get_workspace_credit_usage(workspace_id: 'str', *, period: 'str' = 'monthly', by_resource: 'bool' = False, timeout: 'Optional[float]' = None) -> 'Union[CreditUsage, CreditUsageByResource]'
```

Return usage for the supplied workspace ID. Return type, period choices, `by_resource`, and per-request timeout rules match `get_user_credit_usage()`; workspace access is enforced by the server.

#### Method: `get_organization_credit_usage`

```text
get_organization_credit_usage(organization_id: 'Optional[str]' = None, *, period: 'str' = 'monthly', by_resource: 'bool' = False, timeout: 'Optional[float]' = None) -> 'Union[CreditUsage, CreditUsageByResource]'
```

Return usage for the supplied organization ID, or resolve the authenticated organization through `me()` when omitted. Return type, period choices, grouping, and timeout rules match `get_user_credit_usage()`. Missing current-organization metadata raises `APIError`.

#### Method: `get_user_quotas`

```text
get_user_quotas(user_id: 'Optional[str]' = None, *, timeout: 'Optional[float]' = None) -> 'List[CreditQuota]'
```

Return `list[CreditQuota]`. Omitted `user_id` is resolved through `me()`; the platform must return a JSON array. `timeout` applies to each request. Capacity and remaining-credit formulas are defined by `CreditQuota`.

#### Method: `get_workspace_quotas`

```text
get_workspace_quotas(workspace_id: 'str', *, timeout: 'Optional[float]' = None) -> 'List[CreditQuota]'
```

Return `list[CreditQuota]` for the workspace ID. The platform response must contain a `quotas` list; a missing or malformed collection raises `APIError`. `timeout` overrides each request timeout.

#### Method: `get_organization_quotas`

```text
get_organization_quotas(organization_id: 'Optional[str]' = None, *, timeout: 'Optional[float]' = None) -> 'List[CreditQuota]'
```

Return `list[CreditQuota]` for the organization ID, resolving the current organization through `me()` if omitted. The response must contain a `quotas` list. Missing current-organization metadata or a malformed collection raises `APIError`.

#### Method: `get_organization`

```text
get_organization(organization_id: 'str') -> 'Organization'
```

Return an organization by ID.

#### Method: `update_organization`

```text
update_organization(organization_id: 'str', *, name: 'str') -> 'Dict[str, Any]'
```

Rename an organization and return the platform confirmation.

#### Method: `delete_organization`

```text
delete_organization(organization_id: 'str') -> 'Dict[str, Any]'
```

Delete an organization and return the platform confirmation.

#### Method: `invite_member`

```text
invite_member(organization_id: 'str', *, email: 'str', role: 'str') -> 'Dict[str, Any]'
```

Invite one organization member with the requested role.

#### Method: `invite_members`

```text
invite_members(organization_id: 'str', users: 'List[Dict[str, str]]') -> 'Dict[str, Any]'
```

Accept an actual nonempty `list[dict]`; each dictionary must contain nonempty stripped `email` and `role` strings and no other keys. Validate the full batch before sending it and return the server's acknowledgement `dict`. This does not establish a per-member outcome beyond the returned payload.

#### Method: `list_invitations`

```text
list_invitations(organization_id: 'str') -> 'List[Invitation]'
```

Return invitations for an organization.

#### Method: `cancel_invitation`

```text
cancel_invitation(invitation_id: 'str') -> 'Dict[str, Any]'
```

Cancel an invitation and return the platform confirmation.

#### Method: `update_member_role`

```text
update_member_role(organization_id: 'str', user_id: 'str', *, role: 'str') -> 'Dict[str, Any]'
```

Update an organization member's role.

#### Method: `can_invite_members`

```text
can_invite_members(organization_id: 'str') -> 'Dict[str, Any]'
```

Return invitation capacity and permission data.

#### Method: `get_organization_statistics`

```text
get_organization_statistics(organization_id: 'str') -> 'Dict[str, Any]'
```

Return aggregate organization statistics.

#### Method: `get_organization_compute_resources`

```text
get_organization_compute_resources(organization_id: 'str') -> 'List[ComputeResource]'
```

Return compute resources visible to an organization.

#### Method: `create_workspace`

```text
create_workspace(organization_id: 'str', *, name: 'str', description: 'str' = '') -> 'Workspace'
```

Create a workspace using the supplied organization ID, required name, and string description. Resolve the owner from `me()` and return `Workspace`; missing owner metadata raises `APIError`. The default description is empty.

#### Method: `get_workspace`

```text
get_workspace(workspace_id: 'str') -> 'Workspace'
```

Return a workspace by ID.

#### Method: `update_workspace`

```text
update_workspace(workspace_id: 'str', *, name: 'str', description: 'str' = '') -> 'Workspace'
```

Fetch the current workspace, send the supplied name and description, merge returned fields into the current record, and return validated `Workspace`. Omitting `description` sends `""`, not the old description. This is a server write preceded by a read, not an atomic read/modify/write transaction.

#### Method: `delete_workspace`

```text
delete_workspace(workspace_id: 'str') -> 'Dict[str, Any]'
```

Delete a workspace and return the platform confirmation.

#### Method: `list_workspaces`

```text
list_workspaces(organization_id: 'str', *, page: 'int' = 1, page_size: 'int' = 10) -> 'WorkspaceList'
```

Return a page of workspaces for an organization.

#### Method: `list_my_workspaces`

```text
list_my_workspaces(*, page: 'int' = 1, page_size: 'int' = 10, search: 'Optional[str]' = None) -> 'WorkspaceList'
```

Return a page of workspaces visible to the authenticated user.

#### Method: `list_workspace_members`

```text
list_workspace_members(workspace_id: 'str') -> 'List[UserSummary]'
```

Return members of a workspace.

#### Method: `add_workspace_member`

```text
add_workspace_member(workspace_id: 'str', *, user_id: 'Optional[str]' = None, email: 'Optional[str]' = None) -> 'Dict[str, Any]'
```

Require exactly one of `user_id` or `email`; both/none raise `ValueError`. Validate the workspace ID and selected nonempty selector, then return the server acknowledgement `dict`. User IDs use path-ID validation; email selectors use stripped nonempty-string validation.

#### Method: `remove_workspace_member`

```text
remove_workspace_member(workspace_id: 'str', user_id: 'str') -> 'Dict[str, Any]'
```

Remove one workspace member.

#### Method: `add_workspace_members`

```text
add_workspace_members(workspace_id: 'str', emails: 'List[str]') -> 'Dict[str, Any]'
```

Accept an actual nonempty `list[str]` of nonempty stripped email selectors. Validate every entry before the request and return the server acknowledgement `dict`; the server determines individual membership outcomes.

#### Method: `create_project`

```text
create_project(workspace_id: 'str', *, name: 'str', description: 'str' = '') -> 'Project'
```

Create a project with the supplied workspace ID, required name, and string description; return `Project`. The default description is empty, and the server assigns ownership.

#### Method: `get_project`

```text
get_project(project_id: 'str') -> 'Project'
```

Return a project by ID.

#### Method: `update_project`

```text
update_project(project_id: 'str', *, name: 'str', description: 'str' = '') -> 'Project'
```

Fetch the project, send name and description, merge returned fields into the current record, and return validated `Project`. Omitting `description` sends the empty string. Concurrency behaviour matches `update_workspace()`.

#### Method: `delete_project`

```text
delete_project(project_id: 'str') -> 'Dict[str, Any]'
```

Delete a project and return the platform confirmation.

#### Method: `list_projects`

```text
list_projects(workspace_id: 'str', *, page: 'int' = 1, page_size: 'int' = 10, search: 'Optional[str]' = None) -> 'ProjectList'
```

Return a page of projects in a workspace.

#### Method: `list_my_projects`

```text
list_my_projects(*, page: 'int' = 1, page_size: 'int' = 10) -> 'ProjectList'
```

Return a page of projects visible to the authenticated user.

#### Method: `list_project_members`

```text
list_project_members(project_id: 'str') -> 'List[UserSummary]'
```

Return members of a project.

#### Method: `add_project_member`

```text
add_project_member(project_id: 'str', *, user_id: 'Optional[str]' = None, email: 'Optional[str]' = None) -> 'Dict[str, Any]'
```

Require exactly one of `user_id` or `email` and return the server acknowledgement `dict`. Selector validation and both/none errors match `add_workspace_member()`.

#### Method: `remove_project_member`

```text
remove_project_member(project_id: 'str', user_id: 'str') -> 'Dict[str, Any]'
```

Remove one project member.

#### Method: `create_experiment`

```text
create_experiment(project_id: 'str', *, name: 'str', description: 'str' = '') -> 'Experiment'
```

Create an experiment using the project ID, required name, and string description. Resolve its owner through `me()` and return `Experiment`; missing owner metadata raises `APIError`. The default description is empty.

#### Method: `get_experiment`

```text
get_experiment(experiment_id: 'str') -> 'Experiment'
```

Return an experiment by ID.

#### Method: `update_experiment`

```text
update_experiment(experiment_id: 'str', *, name: 'str', description: 'str' = '') -> 'Experiment'
```

Fetch the experiment, send name and description, merge returned fields into the current record, and return validated `Experiment`. Omitting `description` sends the empty string. Concurrency behaviour matches `update_workspace()`.

#### Method: `delete_experiment`

```text
delete_experiment(experiment_id: 'str') -> 'Dict[str, Any]'
```

Delete an experiment and return the platform confirmation.

#### Method: `list_experiments`

```text
list_experiments(project_id: 'str', *, page: 'int' = 1, page_size: 'int' = 10, search: 'Optional[str]' = None) -> 'ExperimentList'
```

Return a page of experiments in a project.

#### Method: `list_my_experiments`

```text
list_my_experiments(*, page: 'int' = 1, page_size: 'int' = 10) -> 'ExperimentList'
```

Return a page of experiments visible to the authenticated user.

#### Method: `list_workspace_experiments`

```text
list_workspace_experiments(workspace_id: 'str', *, page: 'int' = 1, page_size: 'int' = 10) -> 'ExperimentList'
```

Return a page of experiments in a workspace.

#### Method: `list_experiment_members`

```text
list_experiment_members(experiment_id: 'str') -> 'List[UserSummary]'
```

Return members of an experiment.

#### Method: `add_experiment_member`

```text
add_experiment_member(experiment_id: 'str', user_id: 'str') -> 'Dict[str, Any]'
```

Add one experiment member by user ID.

#### Method: `remove_experiment_member`

```text
remove_experiment_member(experiment_id: 'str', user_id: 'str') -> 'Dict[str, Any]'
```

Remove one experiment member.

#### Method: `associate_compute_resource`

```text
associate_compute_resource(experiment_id: 'str', compute_resource_id: 'str') -> 'Dict[str, Any]'
```

Associate an experiment with a discovered compute resource.

#### Method: `default_problem_name`

```text
default_problem_name(method: 'str', detail: 'Optional[str]' = None) -> 'str'
```

Return `SDK {METHOD} problem[: {detail}] - {timestamp}`, where `METHOD` is converted to uppercase and `timestamp` is the current UTC ISO 8601 time with millisecond precision and a trailing `Z`. `method` and a supplied `detail` must be non-empty strings after stripping.

#### Method: `resolve_experiment`

```text
resolve_experiment(name: 'str', *, timeout: 'Optional[float]' = None) -> 'str'
```

Strip and validate `name`, resolve its exact match within the authenticated organization, and return the experiment ID as `str`. Successful lookups are cached per client/name; a cached lookup performs no new request. This method does not create an experiment. A response without an experiment ID raises `APIError`. `timeout` overrides an uncached request's timeout.

#### Method: `create_problem`

```text
create_problem(payload: 'Dict[str, Any]', *, timeout: 'Optional[float]' = None) -> 'Problem'
```

Require an actual `dict` containing nonempty `name` and `method` strings. Copy the payload, normalize those two fields, pass other fields to the platform, and return `Problem`. This method does not validate a problem-class payload as fully as its high-level solver; prefer the solver's `submit()` when creating solver work.

#### Method: `get_problem`

```text
get_problem(problem_id: 'str', *, timeout: 'Optional[float]' = None) -> 'Problem'
```

Return a platform problem by ID.

#### Method: `list_problems`

```text
list_problems(*, page: 'int' = 1, page_size: 'int' = 10) -> 'ProblemList'
```

Return a page of platform problems.

#### Method: `update_problem`

```text
update_problem(problem_id: 'str', payload: 'Dict[str, Any]') -> 'Problem'
```

Require a nonempty payload `dict`; copy it and normalize `name` and `method` if supplied. Other fields are passed to the service. Return the validated `Problem` from the update response. Invalid IDs or payloads raise `ValueError` before the write request.

#### Method: `delete_problem`

```text
delete_problem(problem_id: 'str', *, timeout: 'Optional[float]' = None) -> 'Dict[str, Any]'
```

Delete a problem and return the platform confirmation.

#### Method: `submit_job`

```text
submit_job(problem_id: 'str', *, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, compute_resource_id: 'Optional[str]' = None, method: 'Optional[str]' = None, scheduled_at: 'Optional[str]' = None, timeout: 'Optional[float]' = None) -> 'JobSubmission'
```

Submit the existing problem ID and return `JobSubmission`. Optional experiment selectors follow [experiment precedence](platform-client.md#experiment-selection); a supplied ID takes precedence over a name. Optional `compute_resource_id` pins a platform resource; `method` is a nonempty string passed to the service. `scheduled_at` requires a timezone-aware ISO 8601 string and requests scheduled submission. `timeout` applies independently to experiment lookup and job creation. This operation can consume credits and is not retried automatically.

#### Method: `schedule_job`

```text
schedule_job(problem_id: 'str', scheduled_at: 'str', *, experiment_id: 'Optional[str]' = None, experiment_name: 'Optional[str]' = None, compute_resource_id: 'Optional[str]' = None, method: 'Optional[str]' = None) -> 'JobSubmission'
```

Schedule an existing problem and return `JobSubmission`. `scheduled_at` is required and follows `validate_scheduled_at()`. Experiment precedence, resource pinning, method validation, and paid-work implications match `submit_job()`; this method uses the client request timeout and the dedicated scheduling operation.

#### Method: `get_job`

```text
get_job(job_id: 'str', *, timeout: 'Optional[float]' = None) -> 'Job'
```

Return a platform job by ID.

#### Method: `cancel_job`

```text
cancel_job(job_id: 'str') -> 'Dict[str, Any]'
```

Request job cancellation and return the platform confirmation.

#### Method: `list_jobs`

```text
list_jobs(*, page: 'int' = 1, page_size: 'int' = 10, period: 'Optional[str]' = None, status: 'Optional[JobStatus | str]' = None, compute_resource_id: 'Optional[str]' = None) -> 'JobList'
```

Return `JobList` for the authenticated user. `page`/`page_size` are positive integers. Optional `period` and `compute_resource_id` are nonempty stripped strings. `status` accepts the five non-unknown `JobStatus` members or exactly their lowercase values, with surrounding whitespace ignored; uppercase strings and `"unknown"` raise `ValueError`.

`period` recognizes `daily`, `weekly`, and `monthly`; `None` leaves dates unrestricted. Other nonempty strings are forwarded but apply no time filter. See [Reading and filtering jobs](platform-client.md#reading-and-filtering-jobs) for time boundaries and resource-filter scope.

#### Method: `list_problem_jobs`

```text
list_problem_jobs(problem_id: 'str', **params: 'Any') -> 'JobList'
```

Return `JobList` for the supplied problem ID. Accepted `params` are only `page`, `page_size`, `period`, `status`, and `compute_resource_id`; unknown names raise `ValueError`. Pagination uses positive integers; period/resource filters are nonempty strings. Status and period choices match `list_jobs()`. The platform problem-job endpoint ignores `compute_resource_id`.

#### Method: `list_experiment_jobs`

```text
list_experiment_jobs(experiment_id: 'Optional[str]' = None, *, experiment_name: 'Optional[str]' = None, **params: 'Any') -> 'JobList'
```

Resolve the experiment using the standard ID/name precedence and return `JobList`. Accepted `params` and validation match `list_problem_jobs()`; lookup can use the per-client experiment-name cache.

#### Method: `list_solvers`

```text
list_solvers(*, method: 'Optional[str]' = None, active_only: 'bool' = False) -> 'List[ComputeResource]'
```

Return `list[ComputeResource]`. `active_only` requires a Boolean. A supplied `method` is split into uppercase alphanumeric tokens and matched as a prefix of the resource's `type`, or its `backend_name` when type is absent. A method with no alphanumeric token raises `ValueError`. `active_only=True` keeps status `active` ignoring case. Discovery does not reserve a resource.

#### Method: `get_solver`

```text
get_solver(resource_id: 'str') -> 'ComputeResource'
```

Return one compute resource by ID.

#### Method: `upload_file`

```text
upload_file(path: 'Path', *, user_id: 'Optional[str]' = None, organization_id: 'Optional[str]' = None, workspace_id: 'Optional[str]' = None, project_id: 'Optional[str]' = None, timeout: 'Optional[float]' = None) -> 'Dict[str, Any]'
```

Accept a path convertible to `Path`, require an existing regular file, and upload its bytes under the basename. A missing file raises `FileNotFoundError`. Optional ownership IDs are validated; omitted user or organization IDs are resolved through `me()`, with `APIError` if unavailable. `timeout` applies independently to lookup and upload. The file handle is closed after the request.

Return the raw nested `storage` dictionary. Missing/non-dictionary storage raises `APIError`; individual fields, including the conventional `ID`, are not validated here. Validate the storage identifier before using it in another operation.

#### Method: `download_result`

```text
download_result(s3_path: 'str', *, timeout: 'Optional[float]' = None) -> 'Any'
```

Accept a nonempty stripped S3-path string, obtain its presigned download URL, fetch the object, and return the decoded JSON value (`Any`). `timeout` applies separately to the platform request and storage GET. Missing URL metadata or non-JSON content raises `APIError`; transport errors propagate. The client does not save a local file.

#### Method: `download_result_by_id`

```text
download_result_by_id(storage_id: 'str', *, timeout: 'Optional[float]' = None) -> 'Any'
```

Resolve the storage ID to a presigned URL and return the downloaded JSON value (`Any`). Request timing, malformed URL metadata, JSON failures, and transport behaviour match `download_result()`.

#### Method: `delete_storage`

```text
delete_storage(storage_id: 'str', *, timeout: 'Optional[float]' = None) -> 'Dict[str, Any]'
```

Delete a storage record and return the platform confirmation.

#### Method: `stream_job`

```text
stream_job(job_id: 'str', *, connect_timeout: 'Optional[float]' = None, read_timeout: 'float' = 30.0) -> 'Iterator[SSEEvent]'
```

Return a generator of `SSEEvent` for the job ID. Validation and HTTP connection occur when iteration starts. `connect_timeout=None` uses the client timeout; both timeout values must be positive finite non-Boolean real numbers. The iterator decodes UTF-8 and dispatches blank-line-delimited events; JSON data becomes any decoded JSON value and other data remains text. See [SSE parsing and closure](platform-client.md#server-sent-events-and-waiting). Close the generator if iteration stops early.

#### Method: `wait_for_job`

```text
wait_for_job(job_id: 'str', *, timeout: 'float' = 5400.0, poll_interval: 'float' = 5.0, use_sse: 'bool' = True) -> 'Job'
```

Monitor the existing job and return its completed `Job`. `timeout` and `poll_interval` require positive finite non-Boolean real values; `use_sse` requires Boolean. Read current state first, optionally make up to three SSE connection attempts, then poll. Transient HTTP statuses and transport exceptions can trigger monitoring retries; no job is resubmitted.

Failed/cancelled states raise `JobFailedError`/`JobCancelledError`; unknown states raise `APIError`; exhausted monitoring budget raises `TimeoutError`. The budget does not cancel the server job or guarantee that in-flight requests finish within that wall time. See [waiting](platform-client.md#server-sent-events-and-waiting) and [timeouts](platform-client.md#timeouts).

#### Method: `extract_result`

```text
extract_result(job: 'Job', *, timeout: 'Optional[float]' = None) -> 'Any'
```

Require `Job`, otherwise raise `TypeError`. Read inline `server_response`, or fetch `server_response_id` when inline data is `None`; an inline `s3://` string is downloaded, while other strings are parsed as JSON when possible. Unparseable non-S3 text remains text. Unwrap a dictionary/list under `result` or `results` and return it; otherwise return the decoded value unchanged, including `None` when no payload exists.

This low-level method does not itself check the job's lifecycle state or validate a class-specific result. High-level solver retrieval performs those checks. `timeout` applies separately to any required download requests.

#### Method: `health`

```text
health() -> 'bool'
```

Request the authenticated user endpoint with a five-second Requests timeout and return `True` for a successful HTTP status. Return `False` on a Requests transport exception or `APIError`. The method does not parse or validate the response's user JSON.

## Platform record conventions

The following platform records are Pydantic models. Constructors use keyword arguments; `model_validate()`, `model_validate_json()`, `model_dump()`, `model_dump_json()`, and `model_json_schema()` follow the Pydantic API. Platform records preserve unknown service fields as model extras. They are mutable; construction validation does not imply validation of subsequent field assignment.

Required identifiers and required descriptive names/statuses are nonempty strings, stripped where their validators specify. Datetime fields accept Pydantic's supported datetime inputs. Optional fields default to `None` unless their signatures state otherwise. List defaults create a separate empty list for each record. Credit fields declared as integer counts are nonnegative integral values and reject Boolean values; durations and rates are finite nonnegative non-Boolean real numbers normalized to `float`.

Entity records and `CreditQuota` accept either `id` or `ID`; `ComputeResource` also normalizes those aliases to public `id`. Supplying both with different stripped values raises validation error. Timing aliases from service payloads are normalized only for declared duration fields: `execution_time` to `compute_time_s`, `solver_time` to `solver_time_s`, and `total_execution_time` to `total_compute_time_s`. Conflicting duplicate durations raise validation error.

### Collection and submission records

These return types are importable from `qpiai_opt.opt_solvers.platform_models`. All constructors are keyword-only and retain unknown service fields. Every collection below defaults to a separate empty list; `pagination` defaults to a new `Pagination()`.

| Class | Fields, types, and defaults |
| --- | --- |
| `Pagination` | `current_page: int = 1`, `page_size: int = 10`, `total: int = 0`. Strict positive integers for page/size; strict nonnegative integer for total. |
| `ProblemList` | `problems: list[Problem] = []`, `pagination: Pagination`. |
| `JobList` | `jobs: list[Job] = []`, `pagination: Pagination`, `total_compute_time_s: float \| None = None`, `total_credits_used: float \| None = None`. Supplied totals must be finite and nonnegative. |
| `WorkspaceList` | `workspaces: list[Workspace] = []`, `pagination: Pagination`, `summary: Any = None`. |
| `ProjectList` | `projects: list[Project] = []`, `pagination: Pagination`. |
| `ExperimentList` | `experiments: list[Experiment] = []`, `pagination: Pagination`. |
| `JobSubmission` | Required `job: Job`; `message: str = "Job submitted"` (nonempty, stripped); `expected_run_at: datetime \| None = None`. This is the direct `Client` acknowledgement; high-level solver submission responses have their own flat fields. |

## Class: `Problem`

```text
Problem(*, id: str, name: str, method: str, created_at: Optional[datetime.datetime] = None, updated_at: Optional[datetime.datetime] = None, **extra_data: Any) -> None
```

Platform problem record.

Fields: `id`, `name`, `method`, `created_at`, `updated_at`.

## Class: `Job`

```text
Job(*, id: str, status: str, method: Optional[str] = None, problem_id: Optional[str] = None, experiment_id: Optional[str] = None, server_response: Any = None, server_response_id: Optional[str] = None, error: Optional[str] = None, compute_time_s: Optional[float] = None, solver_time_s: Optional[float] = None, created_at: Optional[datetime.datetime] = None, updated_at: Optional[datetime.datetime] = None, cancelled_at: Optional[datetime.datetime] = None, cancellation_reason: Optional[str] = None, credits_used: Optional[int] = None, credits_reserved: Optional[int] = None, **extra_data: Any) -> None
```

Platform job record and lifecycle metadata.

Fields: `id`, `status`, `method`, `problem_id`, `experiment_id`, `server_response`, `server_response_id`, `error`, `compute_time_s`, `solver_time_s`, `created_at`, `updated_at`, `cancelled_at`, `cancellation_reason`, `credits_used`, `credits_reserved`.

### Methods and properties

#### Property: `normalized_status`

```python
normalized_status
```

Return the platform status as a stable SDK lifecycle state.

## Enum: `JobStatus`

```text
JobStatus(value: str) -> JobStatus
```

Normalized lifecycle states used by SDK job operations. Construct from an exact lowercase value listed below; an unrecognized value raises `ValueError`. Use `normalize()` for platform aliases.

| Member | Value | Meaning |
| --- | --- | --- |
| `JobStatus.PENDING` | `"pending"` | Accepted but not yet executing. |
| `JobStatus.RUNNING` | `"running"` | Execution is in progress. |
| `JobStatus.COMPLETED` | `"completed"` | Execution ended and a result may be retrieved. |
| `JobStatus.FAILED` | `"failed"` | Execution ended with an error. |
| `JobStatus.CANCELLED` | `"cancelled"` | The job was cancelled. |
| `JobStatus.UNKNOWN` | `"unknown"` | The platform state is absent or unrecognized. |

### Methods and properties

#### Method: `normalize`

```text
normalize(value: 'Any') -> "'JobStatus'"
```

Class method returning a `JobStatus`. Normalize via lowercase text with surrounding whitespace and underscores removed. `pending`/`queued`/`scheduled` map to pending; `running`/`cancelrequested`/`inprogress` to running; `success`/`completed`/`complete`/`succeeded` to completed; `failed`/`failure`/`error` to failed; `cancelled`/`canceled` to cancelled. Other or absent values return unknown. This is response normalization; list-operation status filters accept a narrower set.

## Class: `ComputeResource`

```text
ComputeResource(*, ID: str, backend_name: Optional[str] = None, type: Optional[str] = None, device_name: Optional[str] = None, status: Optional[str] = None, usage_rate: Optional[float] = None, **extra_data: Any) -> None
```

Discoverable solver deployment for advanced resource pinning.

The generated constructor signature uses the platform validation alias `ID`; `id` is also accepted. Both forms populate the public `id` field. Remaining fields: `backend_name`, `type`, `device_name`, `status`, and non-negative finite `usage_rate`.

## Class: `CreditQuota`

```text
CreditQuota(*, id: str, compute_resource_id: str, allocated_credits: int, used_credits: int = 0, reserved_credits: int = 0, total_credits: Optional[int] = None, top_up_credits: int = 0, top_up_expiry_date: Optional[datetime.datetime] = None, from_date: Optional[datetime.datetime] = None, to_date: Optional[datetime.datetime] = None, user_id: Optional[str] = None, workspace_id: Optional[str] = None, organization_id: Optional[str] = None, compute_resource: Optional[dict[str, Any]] = None, **extra_data: Any) -> None
```

A current credit allocation and its remaining capacity.

Fields: `id`, `compute_resource_id`, `allocated_credits`, `used_credits`, `reserved_credits`, `total_credits`, `top_up_credits`, `top_up_expiry_date`, `from_date`, `to_date`, `user_id`, `workspace_id`, `organization_id`, `compute_resource`.

Supplied credit fields are nonnegative integers, excluding Boolean values. `used_credits` cannot exceed `total_credits` when present, otherwise `allocated_credits`. If both period endpoints are present, `to_date >= from_date`. `compute_resource` must be a dictionary or `None`. Optional ownership IDs normalize an empty string to `None` and otherwise require nonempty text.

### Methods and properties

#### Property: `capacity_credits`

```python
capacity_credits
```

Return `int`: `total_credits` when supplied, otherwise `allocated_credits`.

#### Property: `remaining_credits`

```python
remaining_credits
```

Return `int`: `capacity_credits - used_credits`. This property does not subtract reservations or independently add top-ups.

## Class: `CreditResourceUsage`

```text
CreditResourceUsage(*, compute_resource_id: str, used_credits: int, job_count: int, compute_backend: Optional[str] = None, **extra_data: Any) -> None
```

Credit consumption for one compute resource in a usage report.

Fields: `compute_resource_id`, `used_credits`, `job_count`, `compute_backend`.

The identifier is nonempty and stripped. Both counts are nonnegative integers, excluding Boolean values. An absent or empty backend string becomes `None`; other backend values must be nonempty text.

## Class: `CreditUsage`

```text
CreditUsage(*, used_credits: int, start_date: datetime.datetime, end_date: datetime.datetime, jobs: List[qpiai_opt.opt_solvers.platform_models.CreditUsageJob] = <factory>, **extra_data: Any) -> None
```

Credit consumption for one user, workspace, or organization period.

Fields: `used_credits`, `start_date`, `end_date`, `jobs`.

`used_credits` must be a nonnegative integer, excluding Boolean values; `end_date` cannot precede `start_date`. This model does not require the listed job credits to sum to the total.

## Class: `CreditUsageByResource`

```text
CreditUsageByResource(*, used_credits: int, start_date: datetime.datetime, end_date: datetime.datetime, compute_resource_usages: List[qpiai_opt.opt_solvers.platform_models.CreditResourceUsage] = <factory>, **extra_data: Any) -> None
```

Credit consumption grouped by compute resource for one period.

Fields: `used_credits`, `start_date`, `end_date`, `compute_resource_usages`.

Count and period validation match `CreditUsage`. Additionally, `sum(item.used_credits for item in compute_resource_usages)` must equal `used_credits`; inconsistent grouped reports raise validation error.

## Class: `CreditUsageJob`

```text
CreditUsageJob(*, job_id: str, credits_used: int, name: Optional[str] = None, user_name: Optional[str] = None, device_name: Optional[str] = None, method: Optional[str] = None, status: Optional[str] = None, compute_resource_id: Optional[str] = None, compute_backend: Optional[str] = None, executed_at: Optional[datetime.datetime] = None, **extra_data: Any) -> None
```

One job included in a credit-usage report.

Fields: `job_id`, `credits_used`, `name`, `user_name`, `device_name`, `method`, `status`, `compute_resource_id`, `compute_backend`, `executed_at`.

## Class: `Organization`

```text
Organization(*, id: str, organization_name: str, billing_details: Optional[str] = None, min_booking_minutes: Annotated[Optional[int], Strict(strict=True), Ge(ge=0)] = None, max_booking_minutes: Annotated[Optional[int], Strict(strict=True), Ge(ge=0)] = None, max_daily_minutes: Annotated[Optional[int], Strict(strict=True), Ge(ge=0)] = None, current_seat_count: Annotated[Optional[int], Strict(strict=True), Ge(ge=0)] = None, users: List[qpiai_opt.opt_solvers.platform_models.UserSummary] = <factory>, created_at: Optional[datetime.datetime] = None, updated_at: Optional[datetime.datetime] = None, **extra_data: Any) -> None
```

Organization, limits, and member summaries.

Fields: `id`, `organization_name`, `billing_details`, `min_booking_minutes`, `max_booking_minutes`, `max_daily_minutes`, `current_seat_count`, `users`, `created_at`, `updated_at`.

Booking limits and seat count are strict nonnegative integers when present. If both booking endpoints are present, `min_booking_minutes <= max_booking_minutes`. `users` contains parsed `UserSummary` records and defaults to an empty list.

## Class: `Workspace`

```text
Workspace(*, id: str, owner_id: Optional[str] = None, name: str, description: str = '', organization_id: str, is_default: bool = False, created_at: Optional[datetime.datetime] = None, updated_at: Optional[datetime.datetime] = None, **extra_data: Any) -> None
```

Workspace record.

Fields: `id`, `owner_id`, `name`, `description`, `organization_id`, `is_default`, `created_at`, `updated_at`.

`owner_id` may be omitted; supplied owner, name, and organization values must be nonempty stripped text. `is_default` requires an actual Boolean value. `description` defaults to the empty string.

## Class: `Project`

```text
Project(*, id: str, owner_id: str, name: str, description: str = '', workspace_id: str, is_default: bool = False, created_at: Optional[datetime.datetime] = None, updated_at: Optional[datetime.datetime] = None, **extra_data: Any) -> None
```

Project record.

Fields: `id`, `owner_id`, `name`, `description`, `workspace_id`, `is_default`, `created_at`, `updated_at`.

Owner, name, and workspace identifiers are required nonempty stripped strings. `is_default` requires a Boolean; `description` defaults to the empty string.

## Class: `Experiment`

```text
Experiment(*, id: str, owner_id: str, name: str, description: str = '', project_id: str, workspace_id: Optional[str] = None, compute_resource_id: Optional[str] = None, is_default: bool = False, total_compute_time_s: float = 0.0, total_credits_used: int = 0, created_at: Optional[datetime.datetime] = None, updated_at: Optional[datetime.datetime] = None, **extra_data: Any) -> None
```

Experiment record and aggregate usage.

Fields: `id`, `owner_id`, `name`, `description`, `project_id`, `workspace_id`, `compute_resource_id`, `is_default`, `total_compute_time_s`, `total_credits_used`, `created_at`, `updated_at`.

Required owner, name, and project values are nonempty stripped strings. Optional `workspace_id` accepts `None` or an empty string as absent. `is_default` requires a Boolean. Total compute time is finite and nonnegative; total credits are a nonnegative integer excluding Boolean values.

## Class: `Invitation`

```text
Invitation(*, id: str, organization_id: str, email: str, role: str, status: str, invitation_type: Optional[str] = None, invited_by: Optional[str] = None, expires_at: Optional[datetime.datetime] = None, created_at: Optional[datetime.datetime] = None, updated_at: Optional[datetime.datetime] = None, **extra_data: Any) -> None
```

Organization invitation record.

Fields: `id`, `organization_id`, `email`, `role`, `status`, `invitation_type`, `invited_by`, `expires_at`, `created_at`, `updated_at`.

## Class: `UserSummary`

```text
UserSummary(*, id: str, name: Optional[str] = None, email: Optional[str] = None, role: Optional[str] = None, status: Optional[str] = None, organization_id: Optional[str] = None, is_revoked: Optional[bool] = None, **extra_data: Any) -> None
```

User membership summary returned inside collaboration records.

Fields: `id`, `name`, `email`, `role`, `status`, `organization_id`, `is_revoked`.

Optional `name`, `email`, and `role` must be nonempty stripped text when present. Optional `status` and `organization_id` normalize an empty string to `None`. `is_revoked` accepts only Boolean values or `None`.

## Class: `SSEEvent`

```text
SSEEvent(*, event: str, data: Any, **extra_data: Any) -> None
```

One decoded server-sent job event.

Fields: `event`, `data`.

`event` must be a nonempty string without CR/LF; it is retained without stripping. `data` is the decoded event payload and may have any JSON-compatible shape supplied by the service.

## Exception: `PlatformError`

```python
PlatformError
```

Base exception for SDK solver and platform-client errors.

## Exception: `APIError`

```text
APIError(message: 'str', *, status_code: 'Optional[int]' = None, code: 'Optional[str]' = None, details: 'Any' = None) -> 'None'
```

Subclass of `PlatformError`. Stores `status_code: int | None`, `code: str | None`, and `details: Any` unchanged; `str(error)` is the supplied message. HTTP and response-shape failures can supply a status, while locally detected malformed metadata may leave it `None`.

## Exception: `AuthenticationError`

```text
AuthenticationError(message: 'str', *, status_code: 'Optional[int]' = None, code: 'Optional[str]' = None, details: 'Any' = None) -> 'None'
```

Subclass of `APIError` with the same constructor and attributes. Used for unverifiable local credentials and the platform client's HTTP `401` responses; other HTTP errors from `Client` remain `APIError`. A `401` with code `ACCOUNT_REVOKED` preserves the service message; other `401` responses use the SDK's API-key guidance. Obtain a free Opt API key at [QpiAI Opt](https://opt.qpiai.tech/) or a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/), according to the execution route.

## Exception: `CapabilityError`

```text
CapabilityError(problem_class: 'str', *, requested_mode: 'str', available_providers: 'Mapping[str, tuple[str, ...]]', reason: 'str | None' = None, compatible_alternatives: 'tuple[str, ...]' = ()) -> 'None'
```

Raised when no available provider can solve the detected problem class.

### Methods and properties

#### Method: `as_dict`

```text
as_dict() -> 'dict[str, Any]'
```

Return a `dict` with `error="capability_error"`, `problem_class: str`, `requested_mode: str`, `reason: str`, `available_providers: dict[str, tuple[str, ...]]`, and `compatible_alternatives: list[str]`. Construction copies the supplied capabilities mapping and normalizes alternatives to a tuple; a missing/falsey reason gets the default unsupported-provider explanation.

## Exception: `JobFailedError`

```text
JobFailedError(job_id: 'str', message: 'str') -> 'None'
```

Subclass of `PlatformError`; stores `job_id` and formats the message as `Job {job_id} failed: {message}`.

## Exception: `JobCancelledError`

```text
JobCancelledError(job_id: 'str', message: 'str' = 'cancelled') -> 'None'
```

Subclass of `PlatformError`; stores `job_id` and formats the message as `Job {job_id} was cancelled: {message}`.

## Exception: `LocalLimitExceeded`

```text
LocalLimitExceeded(problem_class: 'str', *, observed: 'Mapping[str, int | float | str]', limits: 'Mapping[str, int | float]', explanation: 'str', next_action: 'str') -> 'None'
```

A structured refusal when a problem exceeds a documented local limit.

### Methods and properties

#### Method: `as_dict`

```text
as_dict() -> 'dict[str, Any]'
```

Return a `dict` with `error="local_limit_exceeded"`, `problem_class: str`, `observed: dict[str, int | float | str]`, `limits: dict[str, int | float]`, `explanation: str`, and `next_action: str`. Construction copies the two supplied mappings and retains the descriptive fields as exception attributes.

## Type alias: `SolveResult`

```text
SolveResult = QUBOSolveResult | HUBOSolveResult | MILPSolveResult | IsingSolveResult | QPSolveResult
```

Union of completed LP, MILP, QP, QUBO, HUBO, and Ising result types. Gate-based optimization algorithms return `QuantumResult` from `qpiai_opt.quantum`; that type is not part of this Python alias. The machine-readable `schemas()["solve_result"]` union also includes `QuantumResult`.

## Class: `SolveInfo`

```text
SolveInfo(*, problem_class: Literal['LP', 'MILP', 'QP', 'QUBO', 'HUBO', 'ISING'], execution_mode: Literal['local', 'platform'], provider: str, solver: str | None = None, method: str | None = None, termination: Literal['optimal', 'completed', 'limit_reached', 'infeasible', 'unbounded', 'failed', 'unknown'], has_usable_solution: bool, solution_feasible: bool | None, optimality_proven: bool | None, guarantee: Literal['global_optimum', 'best_found', 'best_bound', 'feasible_only', 'infeasibility_proven', 'unboundedness_proven', 'none', 'unknown'], source_objective: float | None = None, transformed: bool | None = None, settings: dict[str, int | float | str | bool] = <factory>) -> None
```

Common validated interpretation returned by `result.info()` and carried in ordinary result dumps as `solve_info`. Field meanings are defined in [common solve information](local-solving.md#common-solve-information).

`provider` and non-null `solver`/`method` values are stripped nonempty strings. Literal fields use the exact values in the signature. `source_objective` is `None` or a finite non-Boolean real normalized to `float`. `settings` is an actual dictionary with nonempty string keys and Boolean, integer, finite floating-point, or string values; the default is a separate empty dictionary for each instance. Unknown fields and invalid values raise `pydantic.ValidationError`. Field assignment is frozen; the nested settings dictionary remains mutable.

Construction enforces these relationships:

- `has_usable_solution` is true exactly when `solution_feasible` is true.
- `termination="optimal"`, `optimality_proven=True`, and `guarantee="global_optimum"` must occur together.
- `termination="infeasible"` requires `guarantee="infeasibility_proven"` and no usable solution; `termination="unbounded"` requires `guarantee="unboundedness_proven"`. Each proof guarantee also requires its corresponding termination.
- `guarantee="best_found"` or `"feasible_only"` requires a usable solution.

When attached to a result, the information must match its problem class and execution mode. A non-null `source_objective` must equal the result's objective, and a usable solution requires a non-null result solution. These checks validate the record's consistency; they do not execute a solver or independently verify its mathematical claims.

Fields: `problem_class`, `execution_mode`, `provider`, `solver`, `method`, `termination`, `has_usable_solution`, `solution_feasible`, `optimality_proven`, `guarantee`, `source_objective`, `transformed`, `settings`.

## Class: `IsingSolveResult`

```text
IsingSolveResult(*, problem_class: Literal['ISING'] = 'ISING', execution_mode: Literal['local', 'platform'], job_id: str | None = None, problem_id: str | None = None, solver_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, compute_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, solve_info: qpiai_opt.opt_solvers.result_base.SolveInfo | None = None, num_variables: typing.Annotated[int, Strict(strict=True), Ge(ge=0)], objective: float, variable_assignment: Dict[str, Union[int, float]], solution: List[int]) -> None
```

Completed Ising result. `num_variables` is a strict nonnegative integer; `solution` is a list/tuple of exactly that many finite real -1/+1 spins, excluding Booleans, normalized to Python integers. `objective` is finite and non-Boolean. `variable_assignment` maps nonempty names to finite non-Boolean numeric values and may contain decoded binary or numeric source variables. These shape checks do not independently verify the objective or source feasibility. Unknown fields and invalid values raise `pydantic.ValidationError`. Identity and timing use the [common result contract](local-solving.md#completed-result-contract).

Fields: `problem_class`, `execution_mode`, `job_id`, `problem_id`, `solver_time_s`, `compute_time_s`, `solve_info`, `num_variables`, `objective`, `variable_assignment`, `solution`.

### Methods and properties

#### Method: `runtime`

```text
runtime(unit: 'str' = 's', *, kind: 'str' = 'compute') -> 'float | None'
```

Return the compute or solver duration in seconds, milliseconds, or microseconds. `kind="compute"` is the default; unknown durations return `None`.

#### Method: `info`

```text
info() -> 'SolveInfo'
```

Return the common provider, method, termination, feasibility, proof, guarantee, source-objective, transformation, and settings view.

## Class: `QPSolveResult`

```text
QPSolveResult(*, problem_class: Literal['QP'] = 'QP', execution_mode: Literal['local'] = 'local', job_id: str | None = None, problem_id: str | None = None, solver_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, compute_time_s: typing.Annotated[float | None, Ge(ge=0)] = None, solve_info: qpiai_opt.opt_solvers.result_base.SolveInfo | None = None, sense: Literal['MIN', 'MAX'], status: str, model_status: str, objective: Optional[float] = None, solution: Optional[Dict[str, float]] = None, iteration_count: typing.Annotated[int, Strict(strict=True), Ge(ge=0)] = 0) -> None
```

Continuous quadratic-program result, with `execution_mode="local"`. `sense` is the exact `MIN`/`MAX` literal; `status` is stripped/lowercased, while `model_status` is stripped without changing case. Both status fields must be nonempty. Optional objective and solution values are finite non-Boolean reals; solution keys are nonempty strings. `iteration_count` is a strict nonnegative integer. No incumbent is represented by `objective=None` and/or `solution=None`; inspect `info()` before using values. Unknown fields and invalid values raise `pydantic.ValidationError`. Identity and timing use the [common result contract](local-solving.md#completed-result-contract).

Fields: `problem_class`, `execution_mode`, `job_id`, `problem_id`, `solver_time_s`, `compute_time_s`, `solve_info`, `sense`, `status`, `model_status`, `objective`, `solution`, `iteration_count`.

### Methods and properties

#### Method: `runtime`

```text
runtime(unit: 'str' = 's', *, kind: 'str' = 'compute') -> 'float | None'
```

Return the compute or solver duration in seconds, milliseconds, or microseconds. `kind="compute"` is the default; unknown durations return `None`.

#### Method: `info`

```text
info() -> 'SolveInfo'
```

Return the common provider, method, termination, feasibility, proof, guarantee, source-objective, transformation, and settings view.

## Class: `LocalProvider`

```text
LocalProvider(client: 'Client | None' = None)
```

Execute supported problem classes locally with a registered Opt credential. A missing client raises `AuthenticationError` at construction; a non-`Client` value raises `TypeError`. The compiled runtime verifies the key online before each solve; local computation remains free. Prefer the high-level `solve()` entry point when automatic classification, normalization, and complete compute timing are required.

The public `capabilities` class attribute is a `ProviderCapabilities` record. It has `name: str`, `mode: str`, `problem_classes: tuple[str,...]`, aggregate `exact: bool` and `heuristic: bool`, `requires_authentication: bool`, `diagnostics: tuple[str,...]`, and `problem_capabilities: dict[str,dict[str,bool]]` with class-specific exact/heuristic flags. `capabilities.as_dict()` returns a new dictionary with tuple fields converted to lists and nested capability dictionaries copied. These flags describe available methods, not a guarantee for an individual solve.

### Methods and properties

#### Method: `solve`

```text
solve(problem: 'Any', *, problem_class: 'str', time_limit: 'float | None' = None, mip_rel_gap: 'float | None' = None, node_limit: 'int | None' = None, num_reads: 'int | None' = None, num_sweeps: 'int | None' = None, seed: 'int | None' = None, **options: 'Any') -> 'SolveResult'
```

Execute the supplied problem using its explicit canonical `problem_class`: `LP`, `MILP`, `QP`, `QUBO`, `HUBO`, or `ISING`. This direct provider call does not perform the high-level classification/normalization step. Options use the [local option contracts](local-solving.md#local-options); unsupported options, invalid models, authentication failures, and structural limits propagate as documented there. Return the class-specific result. An unavailable compiled runtime raises `ImportError` with installation instructions. Direct provider timing does not add the high-level end-to-end `compute_time_s` measurement.

## Constant: `LOCAL_LIMITS`

```python
LOCAL_LIMITS: Mapping[str, Mapping[str, int]]
```

Read-only class-to-limit mapping, including read-only nested metric mappings. Keys and inclusive integer thresholds are listed in [structural limits](local-solving.md#structural-limits). Assigning entries raises `TypeError`. Limits govern local execution; inspection reports structural metrics without refusing a model solely because it exceeds them.

## Function: `classify`

```text
classify(problem: 'Any') -> 'str'
```

Return `LP`, `MILP`, `QP`, `QUBO`, `HUBO`, `ISING`, or `QUBIT_HAMILTONIAN` without solving or authenticating. Accept the documented `Model`, native problem, LP/MPS path, and dictionary inputs; native `MILPProblem` returns its declared class. Hamiltonian classification requires the optional QpiAI Quantum SDK and its `QubitHamiltonian` type. A file is read to determine integrality; missing files or unsupported suffixes raise.

Classification identifies a representation; it does not fully revalidate already constructed native problems or every coefficient in a raw dictionary. Use `inspect()` for normalization and structural validation. Models use `Model.validate()` and normalize its `MIP` label to `MILP`. Conflicting or unsupported dictionary markers and ambiguous low-degree tuple-key polynomials raise `ValueError`; unsupported objects raise `TypeError`.

## Function: `inspect`

```text
inspect(problem: 'Any', *, algorithm: 'Any' = None) -> 'dict[str, Any]'
```

Normalize and validate the representation, then return `dict` with `problem_class: str`, `representation: str`, `metrics: dict[str,int]`, and `valid=True`. Invalid inputs raise their validation errors rather than returning `valid=False`. `representation` names the normalized input type; the metric inventory and [inspection procedure](../automation.md#inspection-operations) vary by class. Native problem inputs are revalidated; in-memory linear matrices and QP curvature are checked. Inspection does not require authentication or enforce all local size limits, and `valid=True` is not proof of feasibility or local eligibility.

`algorithm=None` selects structural inspection. An explicit gate-based optimization algorithm configuration adds the validated `quantum` report, including compatibility and statically available circuit metrics. Unsupported algorithm/problem combinations raise before execution. Inspection can compile a `Model` and update its encoding/transformation state, but performs no solve or platform submission.

## Function: `provider_capabilities`

```text
provider_capabilities(*, include_platform: 'bool' = True) -> 'list[dict[str, Any]]'
```

Return a new list containing the local capability dictionary and, by default, the platform dictionary. `include_platform` must be an actual Boolean; other values raise `TypeError`. Each dictionary has `name`, `mode`, `problem_classes`, aggregate `exact`/`heuristic`, `requires_authentication`, `diagnostics`, and the per-class `problem_capabilities` map described above. Use class-specific flags when selecting a method. No client, authentication, resource query, or solve is needed.

## Function: `solve`

```text
solve(problem: 'Any', *, mode: 'str' = 'local', client: 'Client | None' = None, allow_platform: 'bool' = False, **options: 'Any') -> 'SolveResult | QuantumResult'
```

Classify, normalize, and execute a supported problem, returning its class-specific completed result. `mode` is case-insensitive `local`, `platform`, or `auto`, without whitespace stripping; invalid values raise `ValueError`. `allow_platform` must be Boolean or raises `TypeError`. Without `algorithm`, local/auto execution requires an Opt `Client` and fresh online Opt verification; platform execution also requires an Opt `Client` and can consume credits. Auto fallback occurs only after `LocalLimitExceeded` and only with `allow_platform=True`; authentication, validation, infeasibility, and transport failures do not trigger it.

Accepted representations, class-specific options, limits, result semantics, and failure handling are defined in [local solving and routing](local-solving.md). Local `compute_time_s` includes this entry point's normalization and result processing; platform durations remain service-reported. An explicit `algorithm` selects the gate-based optimization route with `execution` configuration and independent QCloud authentication; it rejects `algorithm=None`, Opt `Client`, auto mode, paid fallback, and solver-call options such as `time_limit` or `num_reads`. Obtain a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/). `execution` without `algorithm` raises `ValueError`. See the [gate-based optimization workflow](quantum-optimization.md).

## Function: `convert`

```text
convert(problem: 'Any', target: 'str', **options: 'Any') -> 'Any'
```

Accept a supported model/problem/dictionary or canonical IR and a case-insensitive target string. Return the representation-specific object listed below. Targets include `IR`, `QUBO`, `HUBO`, `ISING`, `LP`, `MPS`, `QUBIT_HAMILTONIAN`, and `OMMX`; support depends on the source type. Whitespace is not stripped.

A non-string target raises `TypeError`; unsupported routes or options raise `ValueError`, or the relevant exporter/compiler's typed error. Conversions do not solve or authenticate. Preserve conversion records for decoding, and distinguish raw minimization QUBO/HUBO exports from source-coordinate Ising dictionaries. The optional gate-based optimization and OMMX targets require their integrations.

### Conversion routes

| Source | Target | Return | Accepted conversion options |
| --- | --- | --- | --- |
| supported object | `IR` | Canonical JSON-safe `dict` | none |
| canonical IR | reconstructed object's target | Same as that native object | same as reconstructed object |
| `Model` | `QUBO` | `(qubo_dict, offset)` | Model QUBO compilation options |
| `Model` | `HUBO` | canonical polynomial `dict` | Model HUBO compilation options |
| `Model` | `ISING` | `dict` | Model QUBO compilation options for a binary model; none for a native spin model |
| `Model` | `LP`, `MPS` | model text `str` | none |
| `Model` | `QUBIT_HAMILTONIAN` | `QubitHamiltonian` | none for native spin models; Model HUBO compilation options otherwise |
| `Model` | `OMMX` | OMMX `Instance` | none |
| `MILPProblem` | A supported `Model` target above | Same as its in-memory or parsed model | same as the model target |
| `QUBOProblem` | `ISING` | `dict` | none |
| `HUBOProblem` | `QUBO` | `HUBOToQUBOConvert` | `strength`, `auxiliary_prefix`, `matrix_format` |
| `HUBOProblem` | `ISING` | source-coordinate Ising `dict` after exact quadratization | `strength`, `auxiliary_prefix`, `matrix_format` |
| `HUBOProblem` | `QUBIT_HAMILTONIAN` | higher-locality diagonal `QubitHamiltonian` | none |
| `IsingProblem` | `QUBO` | `IsingToQUBOConvert` | `matrix_format` |
| `IsingProblem` | `ISING` | source-coordinate Ising `dict` | none |
| `QUBOProblem` or `IsingProblem` | `QUBIT_HAMILTONIAN` | `QubitHamiltonian` | none |
| compatible identity/Z/ZZ `QubitHamiltonian` | `ISING` | `dict` | none |
| compatible identity/Z/ZZ `QubitHamiltonian` | `QUBO` | `IsingToQUBOConvert` | `matrix_format` |

Polynomial dictionaries use the same conversion routes as their normalized native problem objects. `MILPProblem` file parsing remains limited to its documented model-domain scope; unsupported semantics raise. HUBO-to-Hamiltonian conversion uses the exact substitution $x=(1-Z)/2$ and retains higher-locality terms without auxiliary qubits. Its expansion-work estimate, the sum of `2**len(term)` over stored polynomial terms, cannot exceed 100,000; an oversized expansion raises `ModelCompilationError` and requires explicit quadratization. Arbitrary real Pauli Hamiltonians can convert to canonical `IR`; reverse Ising/QUBO conversion requires exactly identity/Z/ZZ terms.

Typed conversions preserve the source `MIN` or `MAX` objective. Conversion objects expose `original_sense`, with `source_objective()` or `original_objective()` reporting source coordinates; the `dict` returned by an `ISING` conversion instead carries source coefficients and a `sense` field. QUBO/Ising to qubit-Hamiltonian conversion uses the exact $s=2x-1$, $z=-s$ map. Reverse conversion rejects X/Y terms, terms above locality two, and every other structure that is not exactly identity/Z/ZZ. HUBO to QUBO additionally exposes `complete_sample()`, `decode()`, and `verify_auxiliaries()`. The [conversion chapters](transformations.md#conversion-map) explain these objects beside their mathematical substitutions.

Unsupported options raise rather than being ignored:

```text
ValueError: IR conversion does not accept options: unknown_option
ValueError: QUBOProblem to ISING does not accept options: matrix_format
ValueError: HUBOProblem to QUBO contains unsupported options: sense
ValueError: IsingProblem to QUBO contains unsupported options: strength
```

## Function: `feasibility`

```text
feasibility(model: 'Model', values: 'dict[str, float]', *, tolerance: 'float' = 1e-06) -> 'dict'
```

Require `Model` and an actual `dict` keyed by source variable names; other container/input types raise `TypeError`. Every source variable must be present, and unknown keys raise `ValueError`. Deterministic auxiliary column names from that model's linear reformulation are accepted. `tolerance` follows `Model.constraint_violations()` and defaults to `1e-6`.

Return `{"feasible": bool, "objective": float, "violations": dict[str, float], "tolerance": tolerance}`. Hard-constraint and domain violations follow the [modeller verification contract](api-modeller.md#method-constraint_violations). The source objective is always evaluated; invalid or unrepresentable objective arithmetic raises instead of returning an incomplete report. No solve or authentication occurs.

For a complete evaluable assignment, every evaluated hard constraint appears in `violations`, including zero residuals; domain keys appear for violated domains. `Model.constraint_violations()` can also diagnose incomplete or malformed assignments without evaluating constraints, whereas `feasibility()` requires complete source coordinates and an evaluable objective. Unknown source keys fail before evaluation:

```text
ValueError: values are missing source variables: 'x'
ValueError: values contain unknown source variables: 'unknown_name'
```

## Function: `submit`

```text
submit(problem: 'Any', *, client: 'Client', **options: 'Any') -> 'dict[str, Any]'
```

Require `client=Client(...)`, normalize a supported problem, and create platform work in one submission attempt. A missing/wrong client raises `TypeError`. LP/MILP accepts a `Model`, supported LP/MPS file path, or `MILPProblem`; local matrix dictionaries are not accepted by the platform. QUBO, HUBO, and Ising use their normalized native representations. QP and generic Hamiltonian submission raise `CapabilityError`; gate-based optimization uses the blocking algorithm route.

Options follow the [platform option mapping](local-solving.md#local-options) and the corresponding [QUBO](qubo-solver.md), [HUBO](hubo-solver.md), or [MILP](milp-solver.md) submission contract. Return a JSON-safe `dict` containing `job_id: str`, normalized `status: str`, `message: str`, `problem_id: str | None`, `detected_class: str`, and `mode="platform"`. Linear submissions also contain `storage_id: str`. Ising is explicitly converted to QUBO for submission; retain the original problem for result decoding.

This call can consume credits. It does not automatically retry an ambiguous failure; preserve identifiers and reconcile existing jobs before a manual retry. Validation, transport, and platform errors propagate. See the [nonblocking procedure](../automation.md#nonblocking-platform-lifecycle).

## Function: `status`

```text
status(client: 'Client', job_id: 'str') -> 'dict[str, Any]'
```

Require a `Client` and nonempty `job_id: str` accepted by `Client.get_job()`. Return `dict` with `job_id: str`, `status` as one of `pending`, `running`, `completed`, `failed`, `cancelled`, or `unknown`, `raw_status: str`, and `error: str | None`. This is a single read; it neither waits for completion nor resubmits. Transport/API errors propagate; a wrong client type raises `TypeError`.

## Function: `result`

```text
result(client: 'Client', job_id: 'str', *, problem: 'Any' = None) -> 'Any'
```

Fetch one job using `Client`. Any normalized lifecycle state other than `completed` raises `RuntimeError`, including pending, failed, and cancelled jobs. Without `problem`, return the extracted platform payload (`Any`): inline or storage-backed data, with supported result wrappers removed. That raw value is not guaranteed to be an SDK `SolveResult`.

With `problem`, normalize the same source representation used for submission and return its typed QUBO, HUBO, Ising, or linear result, restoring source coordinates and feasibility where the supplied representation retains them. Preserve the submitted model or compiled snapshot and its decoding information; passing an edited model can change result interpretation. Unsupported QP restoration raises `CapabilityError`. Wrong client types raise `TypeError`; parsing, transport, and platform errors propagate. This call retrieves existing work and does not submit a job.

## Function: `cancel`

```text
cancel(client: 'Client', job_id: 'str') -> 'dict[str, Any]'
```

Require `Client` and the nonempty job identifier accepted by `Client.cancel_job()`. Return that method's platform acknowledgement as `dict[str, Any]`. Cancellation acceptance does not prove terminal cancellation; call `status()` to observe the lifecycle state. Wrong client types raise `TypeError`; identifier, transport, and platform errors propagate. It does not submit replacement work.

## Function: `schemas`

```text
schemas() -> 'dict[str, Any]'
```

Return a fresh `dict[str, dict]` of JSON schemas for request envelopes, problem/configuration models, results, and platform records. Result schemas use `anyOf` and `$defs` where needed; request envelopes reject unknown outer fields. Callbacks and circuits are constructed through the Python API. Schema generation requires no credentials. See [cross-process request handling](../automation.md#schemas-and-cross-process-requests) for validation and execution.

### Schema inventory

- requests: `inspect_request`, `solve_request`, `submit_request`, `job_request`, `feasibility_request`;
- outputs: `solve_result`, `solve_info`, `qubo_result`, `hubo_result`, `ising_result`, `qp_result`, `milp_result`, `quantum_result`, `quantum_solve_info`, `quantum_sample`, `quantum_logical_circuit`, `quantum_diagnostics`, `job`, `job_submission`, `credit_usage`, `credit_usage_by_resource`, `credit_usage_job`, `credit_resource_usage`, `credit_quota`;
- inputs/configuration: `qubo_problem`, `hubo_problem`, `ising_problem`, `milp_problem`, `milp_solver_config`, `milp_warm_start`, `quantum_run`, `quantum_cvar`, `quantum_warm_start`, `quantum_xy_mixer`.

`submit_request` contains `problem_ir`. `solve_request` does not contain `algorithm` or `execution`. Gate-based optimization configuration schemas validate `QuantumRun`, `CVaR`, `WarmStart`, and `XYMixer`; they do not serialize algorithm instances, custom callbacks, or circuits. The `solve_result` schema includes `QuantumResult` alongside the other result models; `solve_info` and `quantum_solve_info` describe their respective information models. Credentials and authenticated client/session state are excluded.

## Class: `IsingProblem`

Import from `qpiai_opt.opt_solvers.ising`.

```text
IsingProblem(*, problem_type: Literal['ISING'] = 'ISING', num_variables: typing.Annotated[int, Strict(strict=True), Ge(ge=0)], sense: Literal['MIN', 'MAX'] = 'MIN', offset: float = 0.0, variable_names: tuple[str, ...] = (), linear: dict[int, float], quadratic: dict[tuple[int, int], float]) -> None
```

Validated spin objective with coefficients in source coordinates. `num_variables` is a strict nonnegative integer; `linear` and `quadratic` are actual dictionaries with nonnegative non-Boolean integral indices below that count and finite non-Boolean real coefficients. Mirrored couplings aggregate, diagonal couplings contribute to `offset`, and zero coefficients disappear. Offset is finite and non-Boolean. Names are absent as `()` or contain one unique nonempty single-line string per spin. Sense and marker normalize case without stripping; unknown fields and malformed values raise `pydantic.ValidationError`. Construction copies coefficient dictionaries. `from_model()` retains an independent source/decoding snapshot.

Fields: `problem_type`, `num_variables`, `sense`, `offset`, `variable_names`, `linear`, `quadratic`.

### Methods

```text
IsingProblem.from_model(model, **compile_options) -> IsingProblem
energy(sample) -> float
decode(sample) -> dict[str, int | float]
source_objective(sample) -> float
```

`from_model` requires a `Model`, compiles it to the Ising representation, and stores an independent source/decoding snapshot. Native spin models accept no encoding options; other supported source models accept the options of `Model.to_ising_dict`. A failed compilation leaves the model's previous compilation state intact.

The three sample methods accept a complete spin sequence of length `num_variables`, a dictionary covering every integer index, or a dictionary covering every declared name. Mixed partial coverage is insufficient; if both complete views are present they must agree. Extra dictionary keys are ignored. Spins must be finite real -1/+1 values excluding Booleans. Invalid samples raise `ValueError`, and a noniterable sample raises `TypeError`. `energy` evaluates the stored source polynomial including its offset, without negating MAX coefficients; evaluation overflow raises `ValueError`. `decode` returns source coordinates when a model snapshot exists, otherwise declared names or generated `s[index]` names. `source_objective` evaluates the original model after decoding, or returns `energy(sample)` for direct construction. See [sample contracts](ising-modeller.md#evaluate-and-decode).
