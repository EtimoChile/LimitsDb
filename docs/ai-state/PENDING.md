---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-05
---

# Pendientes vigentes

Cola compacta. Cada pendiente debe identificar un artefacto propietario y una
próxima acción. Al resolverlo, retirarlo de esta tabla y conservar en
`DECISIONS.md` cualquier decisión duradera que haya resultado.

Estados permitidos: `OPEN`, `IN_PROGRESS`, `DEFERRED` y `FUTURE`.

| ID | Estado | Ámbito | Pendiente | Artefacto propietario | Próxima acción |
|---|---|---|---|---|---|
| `PEND-001` | `OPEN` | Calidad/tipado | Recuperar la baseline estricta de `mypy`; la ejecución del 2026-10-05 reporta 67 errores en seis archivos. Predominan `unused-ignore`, anotaciones ausentes y dos redefiniciones. | `pyproject.toml`; `limitsdb/core/ldb_utils.py`; `limitsdb/core/ldb_params_config.py`; `limitsdb/core/ldb_ilm_config.py`; `limitsdb/core/ldb_runner.py`; `limitsdb/db/oracle/ldb_engine_impl.py`; `limitsdb/cli/ldb_run.py` | Corregir por categorías sin relajar el modo estricto y ejecutar `poetry run mypy limitsdb` después de cada lote. |
