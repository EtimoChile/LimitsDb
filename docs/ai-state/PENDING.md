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
| `PEND-005` | `OPEN` | Pruebas/seguridad de datos | Aumentar la cobertura de `ldb_runner.py` y del adaptador Oracle en transacciones, rollback, reintentos, paralelismo, idempotencia y fallos parciales. La integración real usará una base Oracle efímera dentro de un runner Linux de GitHub Actions, aislada de la base cloud persistente. | `tests/`; `limitsdb/core/ldb_runner.py`; `limitsdb/db/oracle/ldb_engine_impl.py`; workflow futuro de integración | Añadir primero pruebas con dobles y fixtures; después crear un experimento de CI que levante Oracle Free, espere disponibilidad y mida tiempo, memoria y disco antes de incorporar la suite de integración. |
| `PEND-006` | `OPEN` | Publicación/CI | Completar la barrera de publicación sobre el CI de calidad vigente: publicar sólo desde una versión etiquetada y autorizada, con procedencia y entorno protegidos. | `.github/workflows/quality.yaml`; workflow futuro de release; `pyproject.toml`; `CHANGELOG.md` | Definir el canal y las credenciales protegidas, validar primero en TestPyPI o un registro privado y diseñar la autorización de release; no publicar ni crear tags sin solicitud explícita. |
| `PEND-007` | `OPEN` | Documentación/contrato | Resolver que la configuración acepta `postgres` aunque sólo existe el adaptador Oracle. La instalación de desarrollo y las herramientas de calidad ya están alineadas. | `README.md`; `limitsdb/core/ldb_params_config.py`; `limitsdb/db/ldb_engine_loader.py` | Decidir si PostgreSQL se rechaza hasta implementarse o queda como capacidad futura, y después alinear código, ayuda y documentación con pruebas. |
| `PEND-008` | `OPEN` | Distribución | Definir los canales soportados para contenedor y eventual ejecutable autónomo sin asumir que un binario impide copia o reemplaza las obligaciones de licencia; el wheel ya cuenta con instalación limpia automatizada. | `pyproject.toml`; `README.md`; `.github/workflows/quality.yaml`; configuración futura de empaquetado y release | Evaluar contenedor, PyInstaller o Nuitka con el driver Oracle y documentar la matriz elegida antes de ofrecer binarios. |
