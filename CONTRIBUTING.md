# Contributing to LimitsDb

Thank you for your interest in contributing to **LimitsDb**.

This project is maintained by **Inversiones Etimo SpA** and welcomes
community contributions that improve stability, usability, and functionality.

---

## Types of Contributions

Contributions are welcome in the following areas:

- Bug reports
- Bug fixes
- Documentation improvements
- Performance improvements
- Tests
- Feature proposals

Before implementing large changes, please open an **issue** to discuss the
proposal with the maintainers.

---

## Reporting Issues

If you discover a bug or unexpected behavior, please open an issue and include:

- LimitsDb version
- Python version
- Database engine and version
- Steps to reproduce the issue
- Expected vs actual behavior
- Relevant logs or error messages

Clear reports help us resolve issues faster.

---

## Pull Requests

Pull requests are welcome.

Please ensure that:

- The change is clearly described
- Code follows the project's style conventions
- Tests pass
- Documentation is updated when needed

Small, focused pull requests are preferred.

---

## Development Setup

### Prerequisites

- Python 3.12+
- [Poetry 2.5.1 or newer](https://python-poetry.org/docs/#installation)

### Clone and install

```bash
git clone https://github.com/EtimoChile/LimitsDb.git
cd LimitsDb
poetry install
```

### Run the quality suite

```bash
poetry run pytest
poetry run mypy limitsdb
poetry run ruff check .
poetry run ruff format --check .
poetry run pre-commit run --all-files
poetry build
poetry run twine check dist/*
```

Before opening a pull request, all checks must pass. The CI pipeline runs
these checks (plus a clean-wheel installation) on every push and pull request
targeting `development` and `main`.

### Oracle integration tests

End-to-end tests require a dedicated, disposable Oracle instance — never a
shared or production database. The suite connects as `SYSTEM` and drops and
recreates fixed-name objects (for example `DROP USER LDBT_E2E_SOURCE CASCADE`,
plus `LDBT_`-prefixed roles and tables). In CI they run against an ephemeral
Oracle Database Free container. To run them locally against your own disposable
instance, set the following environment variables and pass the
`oracle_integration` marker:

```bash
export LDB_ORACLE_TEST_DSN=localhost:1521/FREEPDB1
export LDB_ORACLE_TEST_USER=system
export LDB_ORACLE_TEST_PASSWORD=<password>
poetry run pytest -m oracle_integration
```
