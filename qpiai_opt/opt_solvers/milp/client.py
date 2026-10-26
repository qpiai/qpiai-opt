"""QpiAI Platform LP/MILP upload, submission, and result handling."""

from __future__ import annotations

import math
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Optional

import requests

from ...opt_modeller.model import _LP_DECODE_NAMES, _LP_ENCODE_NAMES
from .._linear_file import LinearFileMetadata, read_linear_file_metadata
from ..client import Client
from ..exceptions import APIError, _add_exception_note
from ..platform_models import JobStatus
from ..result_base import (
    _attach_solve_info,
    _attach_transformed_linear_info,
    _common_linear_outcome,
    _normalize_runtime_payload,
)
from .models import (
    JobStatusResponse,
    JobSubmissionResponse,
    MILPProblem,
    MILPSolverConfig,
    SolveResult,
    WarmStart,
)


def _translate_name_mapping(value, translation, description):
    if not isinstance(value, dict) or any(not isinstance(name, str) or not name for name in value):
        raise ValueError(f"{description} must be a dictionary with non-empty string names")
    translated = {name.translate(translation): number for name, number in value.items()}
    if len(translated) != len(value):
        raise ValueError(f"{description} contains conflicting source and LP names")
    return translated


class MILPSolver:
    """Solve LP/MPS models through the QpiAI optimization platform."""

    DEFAULT_URL = Client.DEFAULT_URL
    SUPPORTED_SUFFIXES = (".lp", ".mps", ".lp.gz", ".mps.gz")

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
        self._source_metadata_by_job_id: dict[str, LinearFileMetadata] = {}
        self._source_metadata_by_problem_id: dict[str, LinearFileMetadata] = {}
        self._settings_by_job_id: dict[str, dict] = {}
        self._settings_by_problem_id: dict[str, dict] = {}
        self._source_models_by_job_id: dict[str, object] = {}
        self._source_models_by_problem_id: dict[str, object] = {}

    @classmethod
    def _validate_model_path(cls, model_path: str) -> Path:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"Model file not found: {path}")
        if not any(path.name.lower().endswith(suffix) for suffix in cls.SUPPORTED_SUFFIXES):
            raise ValueError("MILP model must be an LP, MPS, LP.GZ, or MPS.GZ file")
        return path

    def submit(
        self,
        model_path: str | MILPProblem,
        *,
        config: Optional[MILPSolverConfig] = None,
        warm_start: Optional[WarmStart] = None,
        timeout: float = 1200.0,
        name: Optional[str] = None,
        experiment_id: Optional[str] = None,
        experiment_name: Optional[str] = None,
        compute_resource_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> JobSubmissionResponse:
        """Upload one LP/MPS model and submit one platform job."""

        if isinstance(model_path, MILPProblem):
            problem = model_path
            source, selected = problem._execution_input(
                {} if config is None else {"config": config},
                mode="platform",
            )
            arguments = dict(
                config=selected.get("config"),
                warm_start=warm_start,
                timeout=timeout,
                name=name,
                experiment_id=experiment_id,
                experiment_name=experiment_name,
                compute_resource_id=compute_resource_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if problem.model is not None:
                source = deepcopy(source)
                with tempfile.TemporaryDirectory(prefix="qpiai-opt-") as directory:
                    path = Path(directory) / "model.lp"
                    source.to_lp_file(path)
                    response = self.submit(str(path), **arguments)
                self._source_models_by_job_id[response.job_id] = source
                self._source_models_by_problem_id[response.problem_id] = source
                return response
            return self.submit(str(source), **arguments)
        timeout = Client.validate_timeout(timeout, "timeout")
        if experiment_id is None and experiment_name is None:
            experiment_id = self.experiment_id
            experiment_name = self.experiment_name
        if compute_resource_id is None:
            compute_resource_id = self.compute_resource_id
        path = self._validate_model_path(model_path)
        if config is not None and not isinstance(config, MILPSolverConfig):
            raise TypeError("config must be a MILPSolverConfig")
        if warm_start is not None and not isinstance(warm_start, WarmStart):
            raise TypeError("warm_start must be a WarmStart")
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
        if workspace_id is not None:
            workspace_id = Client._validate_path_identifier(workspace_id, "workspace_id")
        if project_id is not None:
            project_id = Client._validate_path_identifier(project_id, "project_id")
        source_metadata = read_linear_file_metadata(path)
        warm_start_payload = (
            warm_start.model_dump(exclude_none=True) if warm_start is not None else None
        )
        if source_metadata.lp_names_encoded and warm_start_payload is not None:
            if warm_start_payload.get("col_values") is not None:
                warm_start_payload["col_values"] = _translate_name_mapping(
                    warm_start_payload["col_values"], _LP_ENCODE_NAMES, "Warm-start column values"
                )
        storage = self.client.upload_file(
            path,
            workspace_id=workspace_id,
            project_id=project_id,
            timeout=timeout,
        )
        storage_id = storage.get("id") or storage.get("ID")
        if not storage_id:
            raise RuntimeError("Storage upload response did not include an ID")
        submitted_config = config if config is not None else MILPSolverConfig()
        payload = {
            "name": name if name is not None else Client.default_problem_name("MILP", path.name),
            "method": "MILP",
            "milp_model_storage_id": storage_id,
            "solver_config": submitted_config.model_dump(exclude_none=True),
        }
        if warm_start_payload is not None:
            payload["warm_start"] = warm_start_payload
        try:
            problem = self.client.create_problem(payload, timeout=timeout)
        except APIError as exc:
            if (
                exc.status_code is not None
                and 400 <= exc.status_code < 500
                and exc.status_code not in {408, 425, 429}
            ):
                try:
                    self.client.delete_storage(storage_id, timeout=timeout)
                except Exception as cleanup_error:
                    _add_exception_note(
                        exc,
                        f"Cleanup of rejected MILP storage {storage_id} also failed: "
                        f"{cleanup_error}",
                    )
            else:
                _add_exception_note(
                    exc,
                    f"MILP problem creation outcome is ambiguous; storage {storage_id} "
                    "was retained for reconciliation",
                )
            raise
        except requests.RequestException as exc:
            _add_exception_note(
                exc,
                f"MILP problem creation outcome is ambiguous; storage {storage_id} "
                "was retained for reconciliation",
            )
            raise
        try:
            submission = self.client.submit_job(
                problem.id,
                experiment_id=experiment_id,
                experiment_name=experiment_name,
                compute_resource_id=compute_resource_id,
                method="MILP",
                timeout=timeout,
            )
        except APIError as exc:
            if (
                exc.status_code is not None
                and 400 <= exc.status_code < 500
                and exc.status_code not in {408, 425, 429}
            ):
                problem_deleted = False
                try:
                    self.client.delete_problem(problem.id, timeout=timeout)
                    problem_deleted = True
                except Exception as cleanup_error:
                    _add_exception_note(
                        exc,
                        f"Cleanup of rejected MILP problem {problem.id} also failed: "
                        f"{cleanup_error}",
                    )
                if problem_deleted:
                    try:
                        self.client.delete_storage(storage_id, timeout=timeout)
                    except Exception as cleanup_error:
                        _add_exception_note(
                            exc,
                            f"Cleanup of rejected MILP storage {storage_id} also failed: "
                            f"{cleanup_error}",
                        )
            else:
                _add_exception_note(
                    exc,
                    f"MILP submission outcome is ambiguous; inspect jobs for problem "
                    f"{problem.id} before retrying",
                )
            raise
        except requests.RequestException as exc:
            _add_exception_note(
                exc,
                f"MILP submission outcome is ambiguous; inspect jobs for problem {problem.id} "
                "before retrying",
            )
            raise
        response = JobSubmissionResponse(
            job_id=submission.job.id,
            status=submission.job.normalized_status.value,
            message=submission.message,
            problem_id=problem.id,
            storage_id=storage_id,
        )
        # Record metadata only after the platform has accepted the job. Failed or
        # ambiguous submissions must not leave a locally retrievable false record.
        self._source_metadata_by_job_id[response.job_id] = source_metadata
        self._source_metadata_by_problem_id[response.problem_id] = source_metadata
        submitted_settings = submitted_config.model_dump(exclude_none=True)
        self._settings_by_job_id[response.job_id] = submitted_settings
        self._settings_by_problem_id[response.problem_id] = submitted_settings
        return response

    def solve(
        self,
        model_path: str | MILPProblem,
        *,
        config: Optional[MILPSolverConfig] = None,
        warm_start: Optional[WarmStart] = None,
        timeout: float = 600.0,
        poll_interval: float = 5.0,
        request_timeout: Optional[float] = None,
        name: Optional[str] = None,
        experiment_id: Optional[str] = None,
        experiment_name: Optional[str] = None,
        compute_resource_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> SolveResult:
        """Submit an LP/MPS job, wait for completion, and return its result."""

        timeout = Client.validate_timeout(timeout, "timeout")
        poll_interval = Client.validate_timeout(poll_interval, "poll_interval")
        request_timeout = Client.validate_timeout(
            getattr(self.client, "timeout", 60.0) if request_timeout is None else request_timeout,
            "request_timeout",
        )
        submission = self.submit(
            model_path,
            config=config,
            warm_start=warm_start,
            timeout=request_timeout,
            name=name,
            experiment_id=experiment_id,
            experiment_name=experiment_name,
            compute_resource_id=compute_resource_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        job = self.client.wait_for_job(
            submission.job_id,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        return self._parse_result(job, timeout=request_timeout)

    def get_status(
        self,
        job_id: str,
        timeout: float = 60.0,
        *,
        model_path: Optional[str] = None,
        problem: Optional[MILPProblem] = None,
    ) -> JobStatusResponse:
        """Return lifecycle state and a result when the linear job is complete."""

        if problem is not None:
            if not isinstance(problem, MILPProblem):
                raise TypeError("problem must be MILPProblem")
            if model_path is not None:
                raise ValueError("provide problem or model_path, not both")
            model_path = problem.model_path
        timeout = Client.validate_timeout(timeout, "timeout")
        source_metadata = (
            read_linear_file_metadata(self._validate_model_path(model_path))
            if model_path is not None
            else None
        )
        job = self.client.get_job(job_id, timeout=timeout)
        result = (
            self._parse_result(
                job,
                source_metadata=source_metadata,
                source_model=problem.model if problem is not None else None,
                timeout=timeout,
            )
            if job.normalized_status == JobStatus.COMPLETED
            else None
        )
        response = JobStatusResponse(
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
        if source_metadata is not None:
            self._source_metadata_by_job_id[job.id] = source_metadata
            if job.problem_id is not None:
                self._source_metadata_by_problem_id[job.problem_id] = source_metadata
        return response

    def get_result(
        self,
        job_id: str,
        timeout: float = 60.0,
        *,
        model_path: Optional[str] = None,
        problem: Optional[MILPProblem] = None,
    ) -> SolveResult:
        """Return a completed LP/MILP result."""

        if problem is not None:
            if not isinstance(problem, MILPProblem):
                raise TypeError("problem must be MILPProblem")
            if model_path is not None:
                raise ValueError("provide problem or model_path, not both")
            model_path = problem.model_path
        timeout = Client.validate_timeout(timeout, "timeout")
        source_metadata = (
            read_linear_file_metadata(self._validate_model_path(model_path))
            if model_path is not None
            else None
        )
        job = self.client.get_job(job_id, timeout=timeout)
        if job.normalized_status != JobStatus.COMPLETED:
            raise RuntimeError(f"MILP job {job_id} is {job.normalized_status.value}, not completed")
        result = self._parse_result(
            job,
            source_metadata=source_metadata,
            source_model=problem.model if problem is not None else None,
            timeout=timeout,
        )
        if source_metadata is not None:
            self._source_metadata_by_job_id[job.id] = source_metadata
            if job.problem_id is not None:
                self._source_metadata_by_problem_id[job.problem_id] = source_metadata
        return result

    def _parse_result(
        self,
        job,
        *,
        source_metadata: Optional[LinearFileMetadata] = None,
        source_model=None,
        timeout: Optional[float] = None,
    ) -> SolveResult:
        result = self.client.extract_result(job, timeout=timeout)
        if not isinstance(result, dict):
            raise RuntimeError(f"MILP job {job.id} returned an invalid result")
        result = _normalize_runtime_payload(result, fallback_s=job.compute_time_s)
        if result.get("compute_time_s") is None and job.compute_time_s is not None:
            result["compute_time_s"] = job.compute_time_s
        if result.get("solver_time_s") is None and job.solver_time_s is not None:
            result["solver_time_s"] = job.solver_time_s
        backend_settings = result.pop("solver_config", None)
        settings = self._settings_by_job_id.get(job.id) or self._settings_by_problem_id.get(
            job.problem_id
        )
        if settings is None and isinstance(backend_settings, dict):
            settings = {
                name: value
                for name, value in backend_settings.items()
                if isinstance(value, (bool, int, float, str))
                and (not isinstance(value, float) or math.isfinite(value))
            }
        source_metadata = (
            source_metadata
            or self._source_metadata_by_job_id.get(job.id)
            or self._source_metadata_by_problem_id.get(job.problem_id)
        )
        if source_metadata is not None:
            reported_class = result.get("problem_class")
            if reported_class is not None and reported_class != source_metadata.problem_class:
                raise RuntimeError(
                    f"MILP job {job.id} reported problem class {reported_class!r}, but its "
                    f"source file is {source_metadata.problem_class}"
                )
            reported_sense = result.get("sense")
            if reported_sense is not None and (
                not isinstance(reported_sense, str)
                or reported_sense.strip().upper() != source_metadata.sense
            ):
                raise RuntimeError(
                    f"MILP job {job.id} reported objective sense {reported_sense!r}, but its "
                    f"source file is {source_metadata.sense}"
                )
            result["problem_class"] = source_metadata.problem_class
            result["sense"] = source_metadata.sense
            if (
                source_metadata.problem_class == "LP"
                and str(result.get("status", "")).strip().lower() == "optimal"
                and result.get("objective") is not None
            ):
                # An optimal LP's objective is its exact mathematical bound.
                # The platform may send zero for the inapplicable MIP bound.
                result["best_bound"] = result["objective"]
        elif (
            result.get("num_nodes") == -1
            and str(result.get("status", "")).strip().lower() == "optimal"
            and result.get("objective") not in (None, 0)
            and result.get("best_bound") == 0
            and result.get("optimality_gap") is None
        ):
            # With no source file, the zero MIP bound on a job without MIP
            # nodes cannot be distinguished from an unavailable bound.
            result["best_bound"] = None
        result["job_id"] = job.id
        result["problem_id"] = job.problem_id
        source_model = (
            source_model
            or self._source_models_by_job_id.get(job.id)
            or self._source_models_by_problem_id.get(job.problem_id)
        )
        names_encoded = source_metadata.lp_names_encoded if source_metadata is not None else False
        if source_metadata is None and source_model is not None:
            source_names = [variable.name for variable in source_model.variables()]
            source_names.extend(
                item.get("label") or getattr(item["constraint"], "name", "")
                for item in source_model.constraints
            )
            source_names.extend(
                getattr(item["constraint"], "name", "") for item in source_model.constraints
            )
            names_encoded = any("[" in name or "]" in name for name in source_names)
        if names_encoded:
            for field in ("solution", "dual_values", "reduced_costs"):
                if result.get(field) is not None:
                    result[field] = _translate_name_mapping(result[field], _LP_DECODE_NAMES, field)
            if isinstance(result.get("ranging"), dict):
                ranging = dict(result["ranging"])
                for field in ("variables", "constraints"):
                    if field in ranging:
                        ranging[field] = _translate_name_mapping(
                            ranging[field], _LP_DECODE_NAMES, f"ranging {field}"
                        )
                result["ranging"] = ranging
            if isinstance(result.get("iis"), dict):
                iis = dict(result["iis"])
                for field in ("col_names", "row_names"):
                    if field in iis:
                        if not isinstance(iis[field], (list, tuple)) or any(
                            not isinstance(name, str) for name in iis[field]
                        ):
                            raise ValueError(f"IIS {field} must contain string names")
                        iis[field] = [name.translate(_LP_DECODE_NAMES) for name in iis[field]]
                result["iis"] = iis
        if source_model is not None:
            result["sense"] = source_model.sense
            result["problem_class"] = source_model.validate(target="MILP")
            if result["problem_class"] == "MIP":
                result["problem_class"] = "MILP"
            if result.get("solution") is not None:
                result["objective"] = float(source_model.evaluate_objective(result["solution"]))
        solve_result = SolveResult.model_validate(result)
        outcome = _common_linear_outcome(
            solve_result.status,
            solve_result.model_status,
            has_solution=solve_result.solution is not None,
        )
        usable = solve_result.solution is not None and outcome in {
            "optimal",
            "feasible",
            "limit_reached",
        }
        guarantee = {
            "optimal": "global_optimum",
            "infeasible": "infeasibility_proven",
            "unbounded": "unboundedness_proven",
        }.get(outcome)
        if guarantee is None:
            guarantee = (
                "best_bound"
                if outcome in {"feasible", "limit_reached"} and solve_result.best_bound is not None
                else "feasible_only"
                if usable
                else "unknown"
                if outcome == "unknown"
                else "none"
            )
        termination = outcome if outcome != "feasible" else "completed"
        interpreted = _attach_solve_info(
            solve_result,
            provider="qpiai-opt-platform",
            solver="QpiAI Opt MILP Solver",
            method={
                "LP": "linear-programming",
                "MILP": "mixed-integer-linear-programming",
            }[solve_result.problem_class],
            termination=termination,
            has_usable_solution=usable,
            solution_feasible=True if usable else None,
            optimality_proven=None if outcome == "unknown" else outcome == "optimal",
            guarantee=guarantee,
            source_objective=(solve_result.objective if solve_result.sense is not None else None),
            transformed=False,
            settings=dict(settings or {}),
        )

        if source_model is not None:
            return _attach_transformed_linear_info(
                interpreted,
                source_feasible=source_model.is_feasible(interpreted.solution)
                if interpreted.solution is not None
                else None,
            )
        return interpreted

    def get_solution(
        self,
        job_id: Optional[str] = None,
        result: Optional[SolveResult] = None,
    ) -> Optional[dict[str, float]]:
        """Return a variable assignment from a job ID or completed result."""

        if job_id is not None and result is not None:
            raise ValueError("Provide either job_id or result, not both")
        if result is None:
            if job_id is None:
                raise ValueError("Must provide job_id or result")
            result = self.get_result(job_id)
        elif not isinstance(result, SolveResult):
            raise TypeError("result must be a MILP SolveResult")
        return result.solution

    def health(self) -> bool:
        """Return whether the platform health endpoint is available."""

        return self.client.health()
