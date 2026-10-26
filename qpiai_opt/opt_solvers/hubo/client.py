"""QpiAI Platform HUBO submission, monitoring, and result decoding."""

from __future__ import annotations

import math
import re
from typing import Dict, Optional, Union

import requests

from ..._numeric import _objective_values_agree, finite_fsum, is_finite_real
from ..client import Client
from ..exceptions import APIError, _add_exception_note
from ..platform_models import JobStatus
from ..result_base import _attach_solve_info, _normalize_runtime_payload
from .models import (
    SOLVER_CONFIG_PARAMS,
    HUBOProblem,
    JobStatusResponse,
    JobSubmissionResponse,
    SolveResult,
    _label_sort_key,
    _validated_hubo_problem,
    validate_solver_config_values,
)


class HUBOSolver:
    """Solve HUBO problems through the QpiAI optimization platform."""

    DEFAULT_URL = Client.DEFAULT_URL
    _ENCODED_LABEL_PREFIX = "__qpiai_label_"

    def __init__(
        self,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        *,
        client: Optional[Client] = None,
        experiment_id: Optional[str] = None,
        experiment_name: Optional[str] = None,
        compute_resource_id: Optional[str] = None,
    ) -> None:
        if api_key is not None:
            api_key = Client.validate_identifier(api_key, "api_key")
        if experiment_id is not None:
            experiment_id = Client._validate_path_identifier(experiment_id, "experiment_id")
        if experiment_name is not None:
            experiment_name = Client.validate_identifier(experiment_name, "experiment_name")
        if compute_resource_id is not None:
            compute_resource_id = Client._validate_path_identifier(
                compute_resource_id,
                "compute_resource_id",
            )
        if client is None:
            self.client = Client(
                api_key=api_key,
                base_url=self.DEFAULT_URL if url is None else url,
                experiment_id=experiment_id,
                experiment_name=experiment_name,
            )
        else:
            if not isinstance(client, Client):
                raise TypeError("client must be a qpiai_opt.opt_solvers.Client")
            if api_key is not None and api_key != client.api_key:
                raise ValueError("api_key conflicts with the supplied client")
            if url is not None and Client.validate_base_url(url) != client.base_url:
                raise ValueError("url conflicts with the supplied client")
            self.client = client
        self.url = self.client.base_url
        self.api_key = self.client.api_key
        self.experiment_id = experiment_id
        self.experiment_name = experiment_name
        self.compute_resource_id = compute_resource_id
        self._label_maps_by_job = {}
        self._label_maps_by_problem = {}
        self._problems_by_job = {}
        self._problems_by_problem = {}

    @classmethod
    def _serialize_label(cls, label) -> str:
        if (
            re.fullmatch(r"[A-Za-z0-9_]+", label)
            and label != "()"
            and not label.startswith(cls._ENCODED_LABEL_PREFIX)
        ):
            return label
        encoded = label.encode("utf-8").hex()
        return f"{cls._ENCODED_LABEL_PREFIX}s_{encoded}"

    @classmethod
    def _serialize_polynomial_key(cls, key) -> str:
        if key == ():
            return "()"
        return ",".join(cls._serialize_label(label) for label in key)

    @classmethod
    def _label_map(cls, problem: HUBOProblem) -> dict:
        return {
            cls._serialize_label(label): label
            for label in (
                problem.variable_names
                if problem.variable_names is not None
                else sorted(
                    {label for term in problem.polynomial_dict for label in term},
                    key=_label_sort_key,
                )
            )
        }

    def _build_payload(
        self,
        problem: Union[HUBOProblem, Dict],
        *,
        _validated: bool = False,
        **solver_params,
    ) -> dict:
        validated = problem if _validated else _validated_hubo_problem(problem)
        labels = {label for term in validated.polynomial_dict for label in term}
        if not labels or len(labels) != validated.num_variables:
            raise ValueError(
                "Platform HUBO requires at least one active variable and cannot retain isolated variables; solve this problem locally"
            )
        payload = validated.model_dump(exclude_none=True)
        payload.pop("variable_names", None)
        solver_config = dict(payload.get("solver_config") or {})
        unset = object()
        explicit_config = solver_params.pop("solver_config", unset)
        if explicit_config is not unset and not isinstance(explicit_config, dict):
            raise ValueError("solver_config must be a dictionary")
        if explicit_config is not unset:
            solver_config.update(explicit_config)
        unknown = set(solver_params) - SOLVER_CONFIG_PARAMS
        if unknown:
            raise ValueError(f"Unsupported HUBO solver parameter: {', '.join(sorted(unknown))}")
        solver_config.update(solver_params)
        if solver_config:
            payload["solver_config"] = validate_solver_config_values(solver_config)
        if payload.get("polynomial_dict"):
            payload["polynomial_dict"] = {
                self._serialize_polynomial_key(key): value
                for key, value in payload["polynomial_dict"].items()
            }
        payload.pop("problem_type", None)
        return payload

    def _cleanup_rejected_problem(self, problem_id: str, error: APIError, timeout: float) -> None:
        if error.status_code is None or not 400 <= error.status_code < 500:
            _add_exception_note(
                error,
                f"HUBO submission outcome is ambiguous; inspect jobs for problem {problem_id} "
                "before retrying",
            )
            return
        if error.status_code in {408, 425, 429}:
            _add_exception_note(
                error,
                f"HUBO submission outcome is ambiguous; inspect jobs for problem {problem_id} "
                "before retrying",
            )
            return
        try:
            self.client.delete_problem(problem_id, timeout=timeout)
        except Exception as cleanup_error:
            _add_exception_note(
                error, f"Cleanup of rejected HUBO problem {problem_id} also failed: {cleanup_error}"
            )

    def submit(
        self,
        problem: Union[HUBOProblem, Dict],
        timeout: float = 1200.0,
        *,
        name: Optional[str] = None,
        experiment_id: Optional[str] = None,
        experiment_name: Optional[str] = None,
        compute_resource_id: Optional[str] = None,
        **solver_params,
    ) -> JobSubmissionResponse:
        """Create one HUBO problem and submit one platform job."""

        timeout = Client.validate_timeout(timeout, "timeout")
        if experiment_id is None and experiment_name is None:
            experiment_id = self.experiment_id
            experiment_name = self.experiment_name
        if compute_resource_id is None:
            compute_resource_id = self.compute_resource_id
        if name is not None:
            name = Client.validate_identifier(name, "name")
        if experiment_id is not None:
            experiment_id = Client._validate_path_identifier(experiment_id, "experiment_id")
        if experiment_name is not None:
            experiment_name = Client.validate_identifier(experiment_name, "experiment_name")
        if compute_resource_id is not None:
            compute_resource_id = Client._validate_path_identifier(
                compute_resource_id,
                "compute_resource_id",
            )
        validated = _validated_hubo_problem(problem)
        payload = self._build_payload(validated, _validated=True, **solver_params)
        validated.solver_config = dict(payload.get("solver_config") or {})
        payload.update(
            {
                "name": name if name is not None else Client.default_problem_name("HUBO"),
                "method": "HUBO",
            }
        )
        created = self.client.create_problem(payload, timeout=timeout)
        try:
            submission = self.client.submit_job(
                created.id,
                experiment_id=experiment_id,
                experiment_name=experiment_name,
                compute_resource_id=compute_resource_id,
                method="HUBO",
                timeout=timeout,
            )
        except APIError as exc:
            self._cleanup_rejected_problem(created.id, exc, timeout)
            raise
        except requests.RequestException as exc:
            _add_exception_note(
                exc,
                f"HUBO submission outcome is ambiguous; inspect jobs for problem {created.id} "
                "before retrying",
            )
            raise
        label_map = self._label_map(validated)
        self._label_maps_by_problem[created.id] = label_map
        self._label_maps_by_job[submission.job.id] = label_map
        self._problems_by_problem[created.id] = validated
        self._problems_by_job[submission.job.id] = validated
        return JobSubmissionResponse(
            job_id=submission.job.id,
            status=submission.job.normalized_status.value,
            message=submission.message,
            problem_id=created.id,
        )

    def solve(
        self,
        problem: Union[HUBOProblem, Dict],
        queue: bool = True,
        poll_interval: float = 5.0,
        timeout: float = 5400.0,
        request_timeout: Optional[float] = None,
        **solver_params,
    ) -> SolveResult:
        """Submit a HUBO job, wait for completion, and return its result."""

        queue = Client.validate_boolean(queue, "queue")
        timeout = Client.validate_timeout(timeout, "timeout")
        poll_interval = Client.validate_timeout(poll_interval, "poll_interval")
        request_timeout = Client.validate_timeout(
            getattr(self.client, "timeout", 60.0) if request_timeout is None else request_timeout,
            "request_timeout",
        )
        submission = self.submit(
            problem,
            timeout=request_timeout,
            **solver_params,
        )
        job = self.client.wait_for_job(
            submission.job_id,
            timeout=timeout,
            poll_interval=poll_interval,
            use_sse=queue,
        )
        result = self._parse_result(job, timeout=request_timeout)
        return result

    def get_status(self, job_id: str, timeout: float = 60.0) -> JobStatusResponse:
        """Return lifecycle state and a result when the HUBO job is complete."""

        timeout = Client.validate_timeout(timeout, "timeout")
        job = self.client.get_job(job_id, timeout=timeout)
        result = (
            self._parse_result(job, timeout=timeout)
            if job.normalized_status == JobStatus.COMPLETED
            else None
        )
        return JobStatusResponse(
            job_id=job.id,
            status=job.normalized_status.value,
            result=result,
            error=job.error,
            submitted_at=job.created_at.isoformat() if job.created_at else None,
            completed_at=job.updated_at.isoformat()
            if job.normalized_status == JobStatus.COMPLETED and job.updated_at
            else None,
            compute_time_s=(result.compute_time_s if result is not None else job.compute_time_s),
            solver_time_s=(result.solver_time_s if result is not None else job.solver_time_s),
        )

    def get_result(
        self,
        job_id: str,
        timeout: float = 60.0,
        *,
        problem: Optional[Union[HUBOProblem, Dict]] = None,
    ) -> SolveResult:
        """Return a completed HUBO result and decode labels when supplied."""

        timeout = Client.validate_timeout(timeout, "timeout")
        job = self.client.get_job(job_id, timeout=timeout)
        if job.normalized_status != JobStatus.COMPLETED:
            raise RuntimeError(f"HUBO job {job_id} is {job.normalized_status.value}, not completed")
        validated = None
        if problem is not None:
            validated = _validated_hubo_problem(problem)
        return self._parse_result(job, problem=validated, timeout=timeout)

    def _parse_result(
        self,
        job,
        *,
        problem: Optional[HUBOProblem] = None,
        timeout: Optional[float] = None,
    ) -> SolveResult:
        payload = self.client.extract_result(job, timeout=timeout)
        if not isinstance(payload, dict):
            raise RuntimeError(f"HUBO job {job.id} returned an invalid result")
        payload = _normalize_runtime_payload(payload, fallback_s=job.compute_time_s)
        if payload.get("compute_time_s") is None and job.compute_time_s is not None:
            payload["compute_time_s"] = job.compute_time_s
        if payload.get("solver_time_s") is None and job.solver_time_s is not None:
            payload["solver_time_s"] = job.solver_time_s
        payload["job_id"] = job.id
        payload["problem_id"] = job.problem_id
        label_map = (
            self._label_map(problem)
            if problem is not None
            else self._label_maps_by_job.get(job.id)
            or self._label_maps_by_problem.get(job.problem_id)
        )
        submitted_problem = self._problems_by_job.get(job.id) or self._problems_by_problem.get(
            job.problem_id
        )
        validated_problem = problem if problem is not None else submitted_problem
        raw_solution = payload.get("solution")
        if not isinstance(raw_solution, dict):
            raise RuntimeError(f"HUBO job {job.id} returned an invalid solution")
        if label_map:
            if len(raw_solution) != len(label_map):
                raise RuntimeError(
                    f"HUBO job {job.id} returned {len(raw_solution)} values "
                    f"for {len(label_map)} variables"
                )
            missing = set(label_map) - set(raw_solution)
            unexpected = set(raw_solution) - set(label_map)
            if missing or unexpected:
                raise RuntimeError(
                    f"HUBO job {job.id} returned mismatched variable labels; "
                    f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}"
                )
            payload["solution"] = [raw_solution[label] for label in label_map]
            payload["variable_assignment"] = {
                label_map[label]: raw_solution[label] for label in label_map
            }
        else:
            ordered_labels = sorted(raw_solution, key=_label_sort_key)
            payload["solution"] = [raw_solution[label] for label in ordered_labels]
            payload["variable_assignment"] = {
                label: raw_solution[label] for label in ordered_labels
            }
        raw_objective = payload.get("objective")
        if raw_objective is not None and not is_finite_real(raw_objective):
            raise ValueError("HUBO solver objective must be a finite real number")
        best_energy = payload.pop("best_energy", None)
        if best_energy is not None:
            if not is_finite_real(best_energy):
                raise ValueError("HUBO best energy must be a finite real number")
        if validated_problem is not None:
            assignment = payload["variable_assignment"]
            if any(value not in (0, 1, False, True) for value in assignment.values()):
                raise ValueError("HUBO solution must contain only binary values")
            try:
                source_objective = finite_fsum(
                    [
                        coefficient * math.prod(int(assignment[label]) for label in term)
                        for term, coefficient in validated_problem.polynomial_dict.items()
                    ]
                )
            except (KeyError, OverflowError) as exc:
                raise RuntimeError(
                    f"HUBO job {job.id} returned a solution that cannot be evaluated"
                ) from exc
            for reported in (raw_objective, best_energy):
                if reported is None:
                    continue
                if not _objective_values_agree(float(reported), source_objective):
                    raise RuntimeError(
                        f"HUBO job {job.id} returned an objective inconsistent with its solution"
                    )
            payload["objective"] = source_objective
        source_model = validated_problem._source_model if validated_problem is not None else None
        if source_model is not None:
            assignment = source_model.decode_hubo_sample(payload["variable_assignment"])
            payload["variable_assignment"] = assignment
            payload["objective"] = float(source_model.evaluate_objective(assignment))
        solve_result = SolveResult.model_validate(payload)
        settings_problem = submitted_problem if submitted_problem is not None else validated_problem
        source_feasible = (
            source_model.is_feasible(solve_result.variable_assignment)
            if source_model is not None
            else True
        )
        return _attach_solve_info(
            solve_result,
            provider="qpiai-opt-platform",
            solver="QpiAI Opt HUBO Solver",
            method="HUBO",
            termination="completed",
            has_usable_solution=source_feasible,
            solution_feasible=source_feasible,
            optimality_proven=False,
            guarantee="best_found" if source_feasible else "none",
            source_objective=(solve_result.objective if validated_problem is not None else None),
            transformed=source_model is not None if validated_problem is not None else None,
            settings=(
                dict(settings_problem.solver_config)
                if settings_problem is not None and settings_problem.solver_config
                else {}
            ),
        )

    def get_solution(
        self,
        job_id: Optional[str] = None,
        result: Optional[SolveResult] = None,
    ) -> list[int]:
        """Return a binary solution from a job ID or completed result."""

        if job_id is not None and result is not None:
            raise ValueError("Provide either job_id or result, not both")
        if result is None:
            if job_id is None:
                raise ValueError("Must provide job_id or result")
            result = self.get_result(job_id)
        elif not isinstance(result, SolveResult):
            raise TypeError("result must be a HUBO SolveResult")
        return result.solution

    def health(self) -> bool:
        """Return whether the platform health endpoint is available."""

        return self.client.health()
