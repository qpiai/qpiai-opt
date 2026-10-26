# Contributing to QpiAI Opt

Thank you for improving QpiAI Opt. Changes should preserve mathematical meaning, keep local and paid execution boundaries explicit, and avoid adding a second modelling system where an adapter is sufficient.

Development takes place in [qpiai/qpiai-opt](https://github.com/qpiai/qpiai-opt). Search [existing issues](https://github.com/qpiai/qpiai-opt/issues) before reporting a [defect](REPORTING_BUGS.md) or proposing a [feature](FEATURE_REQUESTS.md). Fork the repository, create a branch from `main`, and submit a pull request describing the problem, resulting behavior, and verification. Documentation corrections are welcome through the same process.

Guided by William of Ockham's razor (the YAGNI principle).

## Development setup

Python 3.10–3.14 is supported. The public source tree contains the modelling, interoperability, and platform SDK. Local execution uses the native runtime included in official SDK wheels. Install `uv`, then run these commands from the repository root:

```bash
uv sync --group dev --no-install-project
uv pip install --no-deps --only-binary=qpiai-opt qpiai-opt
```

Copy the compiled `_local_runtime` file from the installed package into your checkout's `qpiai_opt/opt_solvers/` directory:

- **Windows:** find it in `.venv/Lib/site-packages/qpiai_opt/opt_solvers/`. Its filename ends in `.pyd`.
- **Linux/macOS:** under `.venv/lib/`, open the directory matching your Python version, then `site-packages/qpiai_opt/opt_solvers/`. Its filename ends in `.so`.

Keep the filename unchanged. The binary is already excluded from Git. Repeat the installation and copy when changing Python versions or updating the runtime version. You can now edit the checkout's Python source and run development commands with `uv run --no-sync`:

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
```

`--no-install-project` installs dependencies without building the checkout. `--no-sync` runs commands without automatically synchronizing the environment.

Tests must be deterministic and must not submit paid platform work. Use controlled HTTP failures to check error handling. Native execution checks require a registered Opt key and connectivity; local gate-based optimization checks require an active QCloud credential and connectivity. Obtain a free Opt API key at [QpiAI Opt](https://opt.qpiai.tech/) or a free QCloud API key at [QpiAI QCloud](https://qcloud.qpiai.tech/). Keep credentials in an ignored local `.env` file.

## Changes and pull requests

1. Open or reference an issue for behavior changes.
2. Keep each change focused and add tests for success and failure behavior.
3. Update the applicable reference page and runnable example.
4. Describe changes to mathematical semantics, API signatures, serialization, dependencies, or platform resource use.
5. Include the relevant validation results and update the affected documentation.

By submitting a contribution, you agree that it is licensed under the [Apache License 2.0](LICENSE).

## Building the documentation

Markdown in `docs/`, `README.md`, and `automation.md` is the editable source. Build the public site:

```bash
uv run --no-project --with-requirements docs/requirements.txt python docs/build.py
```

The build retains the site's navigation, code copying, search, themes, and mathematical notation. It publishes unchanged Markdown sources alongside HTML, links each HTML page to its Markdown source, and generates `llms.txt` pointing to the automation entry point and reference body. `docs/pages.json` controls page order and navigation; add new reference chapters there. The builder rejects omitted Markdown pages, missing local targets, duplicate HTML IDs, and broken HTML fragments. Review the rendered pages at desktop and mobile widths before submitting documentation changes.

## Community guidelines

Use respectful, inclusive language. Discuss the technical merits of a proposal, accept constructive feedback, and protect other contributors' privacy. Harassment, personal attacks, and disclosure of another person's private information are unacceptable. Maintainers may remove inappropriate content or restrict participation to protect the community.

## Reporting defects and security issues

Use the issue templates for ordinary defects and feature proposals. Do not put credentials, private models, account identifiers, or unredacted server responses in public issues.

For a suspected vulnerability, follow [Reporting bugs](REPORTING_BUGS.md) and use the repository's private security reporting channel. Do not open a public issue until maintainers confirm disclosure is safe.
