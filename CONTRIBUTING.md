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

Clone the repository and install development dependencies with Poetry 2.5.1 or
newer:

```bash
git clone https://github.com/<repo>/limitsdb.git
cd limitsdb
poetry install --with dev
poetry run pre-commit install
```

Before opening a pull request, run the same checks enforced by CI:

```bash
poetry run ruff format --check .
poetry run ruff check .
poetry run mypy limitsdb
poetry run pytest
```
