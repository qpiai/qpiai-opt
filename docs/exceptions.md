# Exception reference

Catch the narrowest exception that has a distinct recovery action. Modeller failures derive from `QuboModellerError`; solver, routing, and platform failures derive from `PlatformError`.

`NonlinearExpressionError` and `HigherOrderTermError` derive from `InvalidExpressionError`. `EmptyModelError`, `InvalidQUBOError`, and `InvalidLinearModelError` derive from `ModelValidationError`. Other modeller exceptions below derive directly from `QuboModellerError`. `AuthenticationError` derives from `APIError`, which derives from `PlatformError`; the other solver exceptions derive directly from `PlatformError`.

## Modeller exceptions

```python
from qpiai_opt.opt_modeller.utils.exceptions import (
    BoundsError,
    ConstraintValidationError,
    EmptyModelError,
    EncodingError,
    HigherOrderTermError,
    InvalidExpressionError,
    InvalidLinearModelError,
    InvalidQUBOError,
    ModelCompilationError,
    ModelValidationError,
    NonlinearExpressionError,
    NumericalStabilityError,
    ObjectiveError,
    QuboModellerError,
)
```

| Exception | Raised by | Correction |
| --- | --- | --- |
| `QuboModellerError` | Base for all exceptions in this table. | Catch only when every modelling failure has the same recovery path. |
| `InvalidExpressionError` | Variable names, `Term`/`Expression`, arithmetic, `quicksum()`, `dot()`, evaluation, unsupported powers, non-finite or underflowing coefficients. | Correct the symbolic expression or rescale its coefficients. |
| `NonlinearExpressionError` | A polynomial operation receives a degree outside its declared bound. | Use a supported model class or reformulate the expression. |
| `HigherOrderTermError` | A quadratic-only operation receives a term above degree two. | Use HUBO or reduce the higher-order product explicitly. |
| `BoundsError` | Invalid bounds for integer, continuous, semi-continuous, semi-integer, fraction, or mixed variables. | Supply ordered finite bounds where the domain requires them. |
| `ModelValidationError` | Invalid model names, variable registration, parameter targets, piecewise data, validation targets, QP structure, or incompatible model features. | Inspect the message and correct the model structure before conversion or solve. |
| `EmptyModelError` | Validation, updates, or conversion requiring a missing objective. | Set an objective and register any zero-coefficient variables that must remain in the model. |
| `ObjectiveError` | A missing, malformed, non-finite, or unsupported objective/sense. | Set a finite objective and use `MIN` or `MAX`. |
| `ConstraintValidationError` | Invalid relational, logical, SOS, indicator, custom-penalty, RHS, label, or penalty-weight input. | Correct the relation/domain or choose a safe penalty. |
| `InvalidLinearModelError` | LP/MILP matrix or file conversion finds nonlinear terms, nonlinear constraints, duplicate names, or unsupported domains. | Keep the linear path linear or use QUBO/HUBO/QP as appropriate. |
| `InvalidQUBOError` | QUBO validation, matrix conversion, energy evaluation, or QUBO/Ising conversion receives malformed keys, indices, coefficients, samples, sense, or degree. | Normalize the representation and provide finite quadratic coefficients and binary samples. |
| `EncodingError` | Integer, fractional, continuous, or inequality-slack encoding cannot represent the declared domain within its options. | Add finite bounds, choose one resolution method, increase `max_bits` within 1–53, or use a native linear solve. |
| `NumericalStabilityError` | Encoding, QP matrix construction, compilation, objective interpretation, or local solving would erase or misrepresent a nonzero value at binary64 precision. | Rescale the model or choose a coarser representable discretization. |
| `ModelCompilationError` | QUBO/HUBO/Ising compilation, penalty construction, quadratization, sense restoration, or coefficient aggregation cannot preserve the model. | Inspect the message; reformulate, rescale, or supply a safe explicit strength. |

```python
from qpiai_opt.opt_modeller import Continuous, encode_continuous
from qpiai_opt.opt_modeller.utils.exceptions import EncodingError

try:
    encode_continuous(Continuous("flow", 0, 1), num_bits=8, precision=3)
except EncodingError as error:
    message = str(error)  # choose num_bits or precision, not both
```

## Solver and platform exceptions

```python
from qpiai_opt.opt_solvers import (
    APIError,
    AuthenticationError,
    CapabilityError,
    JobCancelledError,
    JobFailedError,
    LocalLimitExceeded,
    PlatformError,
)
```

```text
APIError(message, *, status_code=None, code=None, details=None)
CapabilityError(problem_class, *, requested_mode, available_providers, reason=None, compatible_alternatives=())
JobFailedError(job_id, message)
JobCancelledError(job_id, message="cancelled")
LocalLimitExceeded(problem_class, *, observed, limits, explanation, next_action)
```

| Exception | Meaning and fields | Recovery |
| --- | --- | --- |
| `PlatformError` | Base for SDK routing, solver, and platform failures. | Catch only for one common application-level failure policy. |
| `APIError` | Platform HTTP/shape error. Fields: `status_code`, `code`, `details`. | Preserve the fields and identifiers; reconcile ambiguous submissions before retrying. |
| `AuthenticationError` | Missing, rejected, or unverifiable local credentials, or a platform HTTP `401` response; subclass of `APIError`. | Supply the appropriate Opt or QCloud credential, check connectivity, or retry after the authentication service recovers. Obtain a free Opt API key at [QpiAI Opt](https://opt.qpiai.tech/) or a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/), according to the execution route. No paid fallback occurs. |
| `CapabilityError` | The selected mode cannot solve the detected class. `as_dict()` returns the class, mode, reason, provider capabilities, and alternatives. | Select a compatible representation/provider or change the explicit mode. |
| `LocalLimitExceeded` | Deterministic local structural refusal. `as_dict()` returns `observed`, `limits`, `explanation`, and `next_action`. | Reduce the model/work setting or explicitly authorize platform execution. |
| `JobFailedError` | Terminal failed job; exposes `job_id`. | Read the platform job error and do not treat it as a completed result. |
| `JobCancelledError` | Terminal cancelled job; exposes `job_id`. | Treat the job as having no solution. |

Transport errors from `requests`, Pydantic `ValidationError`, and built-in `TimeoutError`, `ValueError`, `TypeError`, `KeyError`, `ZeroDivisionError`, `ImportError`, and `FileNotFoundError` remain their native types. The relevant modelling, interoperability, solver, and platform chapters identify which boundary raises each one.
