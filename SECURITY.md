# Security Policy

## Supported versions

Security fixes are provided for the latest released version of LimitsDb
published on [PyPI](https://pypi.org/project/limitsdb/). Please upgrade before
reporting an issue.

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.**

Report them privately by either of these channels:

- GitHub private vulnerability reporting: use the **Report a vulnerability**
  button in the repository's **Security** tab.
- Email: contacto@etimo.cl

Please include:

- LimitsDb version and Python version
- Database engine and version
- A description of the issue and its potential impact
- Steps to reproduce, or a minimal proof of concept
- Any suggested mitigation

Do not include real credentials, decrypted secrets, or production data in your
report.

## What to expect

- Acknowledgement of your report within a reasonable time.
- An assessment and, when confirmed, a fix released with a corresponding
  `CHANGELOG.md` entry.
- Credit for the discovery if you wish, once the fix is published.

## Scope

Of particular interest are issues affecting the handling of encrypted secrets,
connection profiles, generated SQL/PLSQL, and privileged database operations
(DDL, grants, purges, database links).
