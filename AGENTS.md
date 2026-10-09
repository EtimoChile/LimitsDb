# LimitsDb operative constitution

LimitsDb is a configuration-driven Information Lifecycle Management (ILM)
executor. It archives and deletes operational data, so an apparently small
change can affect real data, privileges, or schemas. The following rules are
mandatory for any agent.

## Precedence and sources of truth

In case of conflict, apply this order:

1. `AGENTS.md`: process, boundaries, and documentary authority.
2. `README.md`: public behavior, configuration, CLI, and operational flow.
3. `limitsdb/resources/ilm.example.yml`: example contract for ILM policies;
   the models and validators under `limitsdb/core/` define their current
   executable interpretation.
4. `limitsdb/db/ldb_engines.py`: engine-independent contract; each adapter
   under `limitsdb/db/<engine>/` implements that contract.
5. Current code, configuration, and tests, for verifiable implementation facts.
6. `CHANGELOG.md`, for published history; does not substitute current state.

`CONTRIBUTING.md` governs external contributions. `LICENSE`,
`LICENSE-DUAL.md`, `COMMERCIAL-EDITION-NOTICE.md`, `NOTICE`, and
`THIRD_PARTY_LICENSES.md` are authoritative for licenses, attributions, and
commercial editions. Do not reinterpret or modify those terms as part of an
ordinary technical task.

If two sources at the same rank disagree, do not choose silently. Record the
conflict in `docs/ai-state/PENDING.md` and limit the change to what remains
unambiguous.

## Persistent state and efficient context use

After this file, every task must first read `docs/ai-state/CURRENT.md` and
`docs/ai-state/PENDING.md`. Consult `docs/ai-state/DECISIONS.md` only for
IDs or scopes related to the task; do not reread it indiscriminately as it
grows.

These files are authoritative operational memory, not a new specification:

- `CURRENT.md` summarizes current, verifiable facts;
- `PENDING.md` contains only open work with a next action;
- `DECISIONS.md` preserves durable decisions and avoids reopening alternatives
  without new evidence.

At the close of each interaction, review all three files and modify them only
if the state changed, a pending item arose or was resolved, or a decision was
adopted, replaced, or discarded. Do not use them as a session diary or make
artificial edits.

## Language

All project files — source code, tests, documentation, configuration comments,
and commit messages — are written in **English**. This rule applies to every
file in the repository, including files under `docs/`, `docs/ai-state/`, and
`docs/prompts/`. Do not create or modify files in any other language.

## Architecture and change boundaries

- Keep `limitsdb/core/` engine-independent. Do not introduce SQL, types,
  exceptions, or Oracle-specific assumptions into the core.
- Keep Oracle specifics in `limitsdb/db/oracle/`. A new engine capability must
  first be exposed in `DatabaseEngine` and then implemented and tested in the
  affected adapters.
- Console entry points live in `limitsdb/cli/`; they must delegate business
  logic to the core and preserve useful exit codes for automation.
- Packaged resources live in `limitsdb/resources/`. If added or renamed,
  verify that the packaging configuration in `pyproject.toml` includes them.
- Do not change behavior, configuration format, and documentation simultaneously
  without tests that demonstrate the intended migration or compatibility.
- Preserve Python `>=3.12,<4.0`, strict typing, and the conventions declared
  in `pyproject.toml` and `.pre-commit-config.yaml`, unless an explicit decision
  updates those contracts.

## Security, secrets, and data

- Never version credentials, private DSNs, encryption keys, data dumps,
  personal identifiers, or logs with sensitive values.
- Do not print or copy decrypted secrets. Normal paths must maintain
  `enforce_encrypted_secrets=True`; relaxing that protection requires an
  explicit task and documented justification.
- Treat `SOURCE_ILM`, `HISTORY_ILM`, purges, DDL, grants, users, database
  links, and execution of SQL/PLSQL blocks as destructive or privileged
  operations.
- Do not execute `ldb-run`, `ldb-impl`, or SQL against a real database without
  explicit authorization, an identified environment, and prior review of the
  schema, profile, and action. For automated tests use doubles, fixtures, or
  connections the user has declared disposable.
- Do not weaken validations, state controls, transactions, or error logging to
  make a case pass. A partial failure must preserve sufficient evidence for
  recovery without exposing sensitive data.

## Tests and completion criteria

Any task that generates or modifies tests must first read both normative
documents:

- `docs/prompts/test-authoring.md` — mandatory authoring rules (R1–R7): tests
  must be derived from the specification, not from the implementation.
- `docs/prompts/test-organization.md` — which file each test belongs in: each test file
  covers one functional contract defined in the specification; the file to use
  is determined by the spec section that governs the behavior under test.

Apply a verification proportional to the change. The full sequence is:

```bash
poetry run pytest
poetry run mypy limitsdb
poetry run ruff check .
poetry run ruff format --check .
poetry run pre-commit run --all-files
poetry build
poetry run twine check dist/*
```

Do not claim a check passed if it was not executed in the interaction.
A test that requires a real Oracle instance is not improvised or pointed at an
unknown environment; it is documented as a limitation or pending item.

Every behavior change must include or update tests and the corresponding public
documentation. Every publishable change must evaluate whether a version bump and
`CHANGELOG.md` update are needed; do not perform a release, create tags, or
publish artifacts without an explicit request.

## Governance and ownership

Inversiones Etimo SpA maintains the architecture, roadmap, and releases.
Community contributions are received through issues and pull requests in
accordance with `CONTRIBUTING.md`.

New technical decisions are recorded in `docs/ai-state/DECISIONS.md` when they
are durable, affect more than one task, or rule out a reasonable alternative.
Use consecutive IDs `DEC-NNN`, states `ACTIVE`, `REPLACED`, or `DISCARDED`,
and link to the artifacts where the decision is implemented.
