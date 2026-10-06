---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-06
---

# Pendientes vigentes

Cola compacta. Cada pendiente debe identificar un artefacto propietario y una
próxima acción. Al resolverlo, retirarlo de esta tabla y conservar en
`DECISIONS.md` cualquier decisión duradera que haya resultado.

Estados permitidos: `OPEN`, `IN_PROGRESS`, `DEFERRED` y `FUTURE`.

| ID | Estado | Ámbito | Pendiente | Artefacto propietario | Próxima acción |
|---|---|---|---|---|---|
| `PEND-005` | `IN_PROGRESS` | Pruebas/seguridad de datos | Aumentar la cobertura de `ldb_runner.py` y del adaptador Oracle en transacciones, rollback, reintentos basados en progreso, paralelismo, idempotencia y fallos parciales. Las pruebas unitarias de coordinación y transacciones y la primera suite real sobre Oracle Free efímero ya pasaron; la ampliación cubre recuperación tras DDL parcial y administración idempotente de rol, usuario y grant. | `tests/test_ldb_runner_helpers.py`; `tests/test_oracle_engine_utils.py`; `tests/integration/test_oracle_ephemeral.py`; `.github/workflows/oracle-integration.yaml` | Validar en GitHub los cuatro casos reales disparados automáticamente por `push`; después revisar la cobertura pendiente de paralelismo antes de cerrar y vigilar el margen de disco al actualizar la imagen. |
| `PEND-006` | `OPEN` | Publicación/CI | Completar la barrera de publicación sobre el CI de calidad vigente: publicar sólo desde una versión etiquetada y autorizada, con procedencia y entorno protegidos. | `.github/workflows/quality.yaml`; workflow futuro de release; `pyproject.toml`; `CHANGELOG.md` | Definir el canal y las credenciales protegidas, validar primero en TestPyPI o un registro privado y diseñar la autorización de release; no publicar ni crear tags sin solicitud explícita. |
| `PEND-007` | `OPEN` | Documentación/contrato | Resolver que la configuración acepta `postgres` aunque sólo existe el adaptador Oracle. La instalación de desarrollo y las herramientas de calidad ya están alineadas. | `README.md`; `limitsdb/core/ldb_params_config.py`; `limitsdb/db/ldb_engine_loader.py` | Decidir si PostgreSQL se rechaza hasta implementarse o queda como capacidad futura, y después alinear código, ayuda y documentación con pruebas. |
| `PEND-008` | `OPEN` | Distribución | Definir los canales soportados para contenedor y eventual ejecutable autónomo sin asumir que un binario impide copia o reemplaza las obligaciones de licencia; el wheel ya cuenta con instalación limpia automatizada. | `pyproject.toml`; `README.md`; `.github/workflows/quality.yaml`; configuración futura de empaquetado y release | Evaluar contenedor, PyInstaller o Nuitka con el driver Oracle y documentar la matriz elegida antes de ofrecer binarios. |
