"""QpiAI Platform QUBO submission, monitoring, and result restoration."""

from __future__ import annotations

from typing import Dict, List, Optional, Union

import requests

from ..._numeric import _objective_values_agree, is_finite_real
from ...opt_modeller.formats.qubo import calculate_qubo_energy
from ..client import Client
from ..exceptions import APIError, _add_exception_note
from ..platform_models import JobStatus
from ..result_base import _attach_solve_info, _normalize_runtime_payload
from .models import (
    JobStatusResponse,
    JobSubmissionResponse,
    QUBOProblem,
    SolveResult,
    _validated_qubo_problem,
)


class QUBOSolver:
    """Solve QUBO problems through the QpiAI optimization platform."""

    DEFAULT_URL = Client.DEFAULT_URL

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
        self._result_metadata_by_job = {}
        self._result_metadata_by_problem = {}

    def _to_json_payload(
        self,
        problem: Union[QUBOProblem, Dict],
        *,
        _validated: bool = False,
    ) -> dict:
        validated = problem if _validated else _validated_qubo_problem(problem)
        payload = validated.model_dump(
            exclude_none=True,
            exclude={
                "matrix_format",
                "offset",
                "sense",
                "variable_names",
            },
        )
        if validated.sense == "MAX":
            payload["qubo_dict"] = {
                key: -coefficient for key, coefficient in validated.qubo_dict.items()
            }
        if payload.get("qubo_dict"):
            payload["qubo_dict"] = {
                ",".join(map(str, key)): value for key, value in payload["qubo_dict"].items()
            }
        payload.pop("problem_type", None)
        return payload

    @staticmethod
    def _result_metadata(problem: QUBOProblem) -> dict:
        return {
            "offset": problem.offset,
            "sense": problem.sense,
            "variable_names": problem.variable_names,
            "num_variables": problem.num_variables,
            "source_model": problem._source_model,
            "problem": problem,
        }

    def _cleanup_rejected_problem(self, problem_id: str, error: APIError, timeout: float) -> None:
        if error.status_code is None or not 400 <= error.status_code < 500:
            _add_exception_note(
                error,
                f"QUBO submission outcome is ambiguous; inspect jobs for problem {problem_id} "
                "before retrying",
            )
            return
        if error.status_code in {408, 425, 429}:
            _add_exception_note(
                error,
                f"QUBO submission outcome is ambiguous; inspect jobs for problem {problem_id} "
                "before retrying",
            )
            return
        try:
            self.client.delete_problem(problem_id, timeout=timeout)
        except Exception as cleanup_error:
            _add_exception_note(
                error, f"Cleanup of rejected QUBO problem {problem_id} also failed: {cleanup_error}"
            )

    def submit(
        self,
        problem: Union[QUBOProblem, Dict],
        timeout: float = 1200.0,
        *,
        name: Optional[str] = None,
        experiment_id: Optional[str] = None,
        experiment_name: Optional[str] = None,
        compute_resource_id: Optional[str] = None,
    ) -> JobSubmissionResponse:
        """Create one QUBO problem and submit one platform job."""

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
        validated = _validated_qubo_problem(problem)
        if not validated.qubo_dict:
            raise ValueError(
                "QpiAI Platform requires at least one nonzero QUBO coefficient; "
                "solve zero-energy QUBOs locally"
            )
        payload = self._to_json_payload(validated, _validated=True)
        payload.update(
            {
                "name": name if name is not None else Client.default_problem_name("QUBO"),
                "method": "QUBO",
            }
        )
        created = self.client.create_problem(payload, timeout=timeout)
        try:
            submission = self.client.submit_job(
                created.id,
                experiment_id=experiment_id,
                experiment_name=experiment_name,
                compute_resource_id=compute_resource_id,
                method="QUBO",
                timeout=timeout,
            )
        except APIError as exc:
            self._cleanup_rejected_problem(created.id, exc, timeout)
            raise
        except requests.RequestException as exc:
            _add_exception_note(
                exc,
                f"QUBO submission outcome is ambiguous; inspect jobs for problem {created.id} "
                "before retrying",
            )
            raise
        metadata = self._result_metadata(validated)
        self._result_metadata_by_problem[created.id] = metadata
        self._result_metadata_by_job[submission.job.id] = metadata
        return JobSubmissionResponse(
            job_id=submission.job.id,
            status=submission.job.normalized_status.value,
            message=submission.message,
            problem_id=created.id,
        )

    def solve(
        self,
        problem: Union[QUBOProblem, Dict],
        queue: bool = True,
        poll_interval: float = 5.0,
        timeout: float = 5400.0,
        request_timeout: Optional[float] = None,
        **submit_options,
    ) -> SolveResult:
        """Submit a QUBO job, wait for completion, and return its result."""

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
            **submit_options,
        )
        job = self.client.wait_for_job(
            submission.job_id,
            timeout=timeout,
            poll_interval=poll_interval,
            use_sse=queue,
        )
        return self._parse_result(job, timeout=request_timeout)

    def get_status(self, job_id: str, timeout: float = 60.0) -> JobStatusResponse:
        """Return lifecycle state and a result when the QUBO job is complete."""

        timeout = Client.validate_timeout(timeout, "timeout")
        job = self.client.get_job(job_id, timeout=timeout)
        result = None
        if job.normalized_status == JobStatus.COMPLETED:
            result = self._parse_result(job, timeout=timeout)
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
        problem: Optional[Union[QUBOProblem, Dict]] = None,
    ) -> SolveResult:
        """Return a completed QUBO result and apply source metadata when supplied."""

        timeout = Client.validate_timeout(timeout, "timeout")
        job = self.client.get_job(job_id, timeout=timeout)
        if job.normalized_status != JobStatus.COMPLETED:
            raise RuntimeError(f"QUBO job {job_id} is {job.normalized_status.value}, not completed")
        validated = None
        if problem is not None:
            validated = _validated_qubo_problem(problem)
        return self._parse_result(job, problem=validated, timeout=timeout)

    def _parse_result(
        self,
        job,
        *,
        problem: Optional[QUBOProblem] = None,
        timeout: Optional[float] = None,
    ) -> SolveResult:
        payload = self.client.extract_result(job, timeout=timeout)
        if not isinstance(payload, dict):
            raise RuntimeError(f"QUBO job {job.id} returned an invalid result")
        payload = _normalize_runtime_payload(payload, fallback_s=job.compute_time_s)
        if payload.get("compute_time_s") is None and job.compute_time_s is not None:
            payload["compute_time_s"] = job.compute_time_s
        if payload.get("solver_time_s") is None and job.solver_time_s is not None:
            payload["solver_time_s"] = job.solver_time_s
        payload["job_id"] = job.id
        payload["problem_id"] = job.problem_id
        metadata = (
            self._result_metadata(problem)
            if problem is not None
            else self._result_metadata_by_job.get(job.id)
            or self._result_metadata_by_problem.get(job.problem_id)
        )
        raw_objective = payload.get("objective")
        if raw_objective is not None:
            if not is_finite_real(raw_objective):
                raise ValueError("QUBO solver objective must be a finite real number")
            solver_objective = float(raw_objective)
        if metadata is not None and raw_objective is not None:
            raw_solution = payload.get("solution")
            if not isinstance(raw_solution, (list, tuple)):
                raise RuntimeError(f"QUBO job {job.id} returned an invalid solution")
            if len(raw_solution) != metadata["num_variables"]:
                raise RuntimeError(
                    f"QUBO job {job.id} returned {len(raw_solution)} values "
                    f"for {metadata['num_variables']} variables"
                )
            source_energy = calculate_qubo_energy(
                metadata["problem"].qubo_dict,
                raw_solution,
            )
            expected_solver_objective = (
                -source_energy if metadata["sense"] == "MAX" else source_energy
            )
            if not _objective_values_agree(solver_objective, expected_solver_objective):
                raise RuntimeError(
                    f"QUBO job {job.id} returned an objective inconsistent with its solution"
                )
            payload["objective"] = source_energy + metadata["offset"]
            names = metadata["variable_names"]
        else:
            names = None
        if payload.get("solution") is not None:
            names = names or tuple(f"x[{index}]" for index in range(len(payload["solution"])))
            payload["variable_assignment"] = dict(zip(names, payload["solution"]))
            source_model = metadata.get("source_model") if metadata is not None else None
            if source_model is not None:
                source_assignment = source_model.decode_qubo_sample(payload["solution"])
                payload["variable_assignment"] = source_assignment
                payload["objective"] = float(source_model.evaluate_objective(source_assignment))
        payload.pop("solution_by_variable", None)
        solve_result = SolveResult.model_validate(payload)
        source_model = metadata.get("source_model") if metadata is not None else None
        source_feasible = (
            source_model.is_feasible(solve_result.variable_assignment)
            if source_model is not None
            else True
        )
        # QUBO solver configuration is platform-internal/proprietary and is
        # deliberately not copied into public result information.
        settings = {}
        return _attach_solve_info(
            solve_result,
            provider="qpiai-opt-platform",
            solver="QpiAI Opt QUBO Solver",
            method="QUBO",
            termination="completed",
            has_usable_solution=source_feasible,
            solution_feasible=source_feasible,
            optimality_proven=False,
            guarantee="best_found" if source_feasible else "none",
            source_objective=solve_result.objective if metadata is not None else None,
            transformed=(source_model is not None) if metadata is not None else None,
            settings=settings,
        )

    def get_solution(
        self,
        job_id: Optional[str] = None,
        result: Optional[SolveResult] = None,
    ) -> List[int]:
        """Return a binary solution from a job ID or completed result."""

        if job_id is not None and result is not None:
            raise ValueError("Provide either job_id or result, not both")
        if result is None:
            if job_id is None:
                raise ValueError("Must provide job_id or result")
            result = self.get_result(job_id)
        elif not isinstance(result, SolveResult):
            raise TypeError("result must be a QUBO SolveResult")
        return result.solution

    def health(self) -> bool:
        """Return whether the platform health endpoint is available."""

        return self.client.health()
