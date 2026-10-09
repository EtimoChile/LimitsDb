# LimitsDb — Development Guide

## Setup

Install development dependencies with Poetry 2.5.1 or newer:

```bash
poetry install --with dev
```

Install the Git hook so Ruff, mypy, and pytest run automatically on commits:

```bash
poetry run pre-commit install
# or, if you are using the active Python environment directly:
python -m pre_commit install
```

## Daily workflow

Format, lint, and type-check:

```bash
poetry run ruff format .
poetry run ruff check .
poetry run mypy limitsdb
```

Run the test suite:

```bash
poetry run pytest -q
poetry run coverage run -m pytest -q && poetry run coverage report
```

Run all checks at once without committing:

```bash
poetry run pre-commit run --all-files
```

Pull requests and pushes to `development` or `main` run the same gates in
GitHub Actions, then build and validate the distributions and install the
wheel in a clean environment.

## Managing dependencies

Add a dependency:

```bash
poetry add <package>
```

Update dependencies:

```bash
poetry update
```

## Oracle integration tests

The integration tests are excluded from the default test command. They require
a dedicated Oracle instance — never a shared or production database. The suite
creates and drops objects whose names begin with `LDBT_`, and the E2E tests
provision and tear down entire schemas.

Set three environment variables that point to that instance, then run:

**Linux / macOS**

```bash
export LDB_ORACLE_TEST_DSN=host:1521/SERVICE
export LDB_ORACLE_TEST_USER=system
export LDB_ORACLE_TEST_PASSWORD=<test-only-password>
poetry run pytest -m oracle_integration
```

Or inline for a single run:

```bash
LDB_ORACLE_TEST_DSN=host:1521/SERVICE \
LDB_ORACLE_TEST_USER=system \
LDB_ORACLE_TEST_PASSWORD=<test-only-password> \
poetry run pytest -m oracle_integration
```

**Windows (PowerShell)**

```powershell
$env:LDB_ORACLE_TEST_DSN      = "host:1521/SERVICE"
$env:LDB_ORACLE_TEST_USER     = "system"
$env:LDB_ORACLE_TEST_PASSWORD = "<test-only-password>"
poetry run pytest -m oracle_integration
```

If any variable is missing, the tests are skipped rather than failing.

> **Oracle 12.1 compatibility** — Oracle 12.1 limits identifiers to 30
> characters. The test suite generates passwords within that limit.
> Oracle 12.2+ raised the limit to 128 characters and is not affected.

## CI coverage

The Oracle integration workflow starts an Oracle Database Free container inside
a GitHub Actions Linux runner for relevant pull requests and manual runs. It
measures combined line and branch coverage across the local and E2E suites,
including CLI subprocesses and multiprocessing workers. Text, XML, HTML, and
Coverage data files are retained as a workflow artifact for 14 days.

The end-to-end test exercises the installed `ldb-impl` and `ldb-run` entry
points, provisions separate source and history schemas, and processes related
order/header and line/detail tables through both `SOURCE_ILM` and `HISTORY_ILM`.
An independent table exercises a second worker concurrently. The test verifies
dependency-safe movement and purge results, multiple worker processes, and the
resulting audit records. It then repeats both actions for the same process date
and verifies that completed tables launch no workers or duplicate audit results.
