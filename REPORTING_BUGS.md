# Reporting bugs and security issues

## Ordinary defects

Search [existing issues](https://github.com/qpiai/qpiai-opt/issues), then [open a bug report](https://github.com/qpiai/qpiai-opt/issues/new?template=bug_report.yml) if the defect is not already tracked. Reproduce the problem on a supported Python version and the latest release. Include:

- `python --version` and `python -m pip show qpiai-opt`;
- operating system and architecture;
- the smallest (credential-free) reproducer;
- expected vs. actual behavior;
- complete traceback with API keys, entity IDs, URLs, and private model data redacted;
- whether execution was `local`, `platform`, or `auto`.

For solver behavior, include `type(result).__name__`, `result.model_dump(mode="json")`, the problem classification, and a canonical IR or LP/MPS reproducer only when it contains no confidential data.

## Security policy

Report suspected vulnerabilities through the repository's [private vulnerability reporting page](https://github.com/qpiai/qpiai-opt/security/advisories/new). Do not use a public issue for authentication, credential exposure, authorization bypass, paid-work duplication, dependency compromise, or remote-code-execution concerns.

Include affected versions, impact, reproduction conditions, and mitigations if known. Do not include live API keys. Maintainers will acknowledge the report, coordinate validation and remediation, and agree on disclosure timing with the reporter.
