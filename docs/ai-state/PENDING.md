---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-07
---

# Pendientes vigentes

Cola compacta. Cada pendiente debe identificar un artefacto propietario y una
próxima acción. Al resolverlo, retirarlo de esta tabla y conservar en
`DECISIONS.md` cualquier decisión duradera que haya resultado.

Estados permitidos: `OPEN`, `IN_PROGRESS`, `DEFERRED` y `FUTURE`.

| ID | Estado | Ámbito | Pendiente | Artefacto propietario | Próxima acción |
|---|---|---|---|---|---|
| `PEND-006` | `OPEN` | Publicación/CI | Completar la barrera de publicación sobre el CI de calidad vigente: publicar sólo desde una versión etiquetada y autorizada, con procedencia y entorno protegidos. | `.github/workflows/quality.yaml`; workflow futuro de release; `pyproject.toml`; `CHANGELOG.md` | Definir el canal y las credenciales protegidas, validar primero en TestPyPI o un registro privado y diseñar la autorización de release; no publicar ni crear tags sin solicitud explícita. |
| `PEND-008` | `OPEN` | Distribución | Definir los canales soportados para contenedor y eventual ejecutable autónomo sin asumir que un binario impide copia o reemplaza las obligaciones de licencia; el wheel ya cuenta con instalación limpia automatizada. | `pyproject.toml`; `README.md`; `.github/workflows/quality.yaml`; configuración futura de empaquetado y release | Evaluar contenedor, PyInstaller o Nuitka con el driver Oracle y documentar la matriz elegida antes de ofrecer binarios. |
| `PEND-009` | `OPEN` | Configuración/multibase | Implementar `DEC-011`: usar archivos como única fuente de políticas ILM y retirar el fallback, contrato y DDL de `LDB_CNF`/`LDB_CNF_ID`, sin borrar automáticamente objetos legados existentes. | `limitsdb/core/ldb_runner.py`; `limitsdb/db/ldb_engines.py`; `limitsdb/db/oracle/ldb_engine_impl.py`; `limitsdb/cli/ldb_impl.py`; `README.md`; pruebas | En una sesión dedicada, definir el error ante ausencia de `ilm.yml`, retirar la ruta de base de datos, actualizar documentación y migración compatible, y ajustar las pruebas. |
| `PEND-010` | `OPEN` | ILM/relaciones | Resolver la discrepancia de `source_orphan_purge`: `orphan_check_column` está documentado y se valida, pero no interviene en el predicado ejecutado; además falta definir si un huérfano se elimina directamente o se archiva y con qué fecha de retención histórica. | `README.md`; `limitsdb/resources/ilm.example.yml`; `limitsdb/core/ldb_runner.py`; pruebas unitarias y E2E | Definir la semántica de datos huérfanos antes de implementar el predicado y agregar un caso E2E separado que no debilite restricciones ni retención. |
| `PEND-013` | `IN_PROGRESS` | Pruebas/cobertura | El workflow Oracle genera cobertura combinada de líneas y ramas para pruebas locales y E2E, incluidos subprocesses CLI y workers, pero todavía no existe una baseline verificada del runner. | `.github/workflows/oracle-integration.yaml`; `pyproject.toml`; `README.md` | Ejecutar manualmente el workflow Oracle, verificar el informe y el artefacto, registrar la baseline y decidir por separado si corresponde fijar un umbral. |
