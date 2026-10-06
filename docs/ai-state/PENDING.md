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
| `PEND-003` | `OPEN` | Robustez/errores | Clasificar las 25 capturas amplias de `Exception`, eliminar silencios peligrosos y definir errores de dominio para configuración, conexión, validación y ejecución, conservando causa y evidencia recuperable. | `limitsdb/core/`; `limitsdb/cli/`; `limitsdb/db/oracle/` | Inventariar cada captura como frontera válida o deuda; corregir primero lectura de configuración, secretos y ejecución de workers con pruebas de fallo. |
| `PEND-004` | `OPEN` | Configuración/API | Simplificar el modelo de configuración y sustituir diccionarios dinámicos por contratos tipados donde aporte control, sin cambiar simultáneamente el formato público ni su precedencia. | `limitsdb/core/ldb_params_config.py`; `limitsdb/core/ldb_config_loader.py`; `limitsdb/core/ldb_ilm_config.py`; `limitsdb/resources/ilm.example.yml`; `README.md` | Caracterizar con pruebas el contrato vigente y proponer la separación entre ejecución, conexiones y administración, incluyendo una estrategia de compatibilidad. |
| `PEND-005` | `OPEN` | Pruebas/seguridad de datos | Aumentar la cobertura de `ldb_runner.py` y del adaptador Oracle en transacciones, rollback, reintentos, paralelismo, idempotencia y fallos parciales. | `tests/`; `limitsdb/core/ldb_runner.py`; `limitsdb/db/oracle/ldb_engine_impl.py` | Añadir primero pruebas con dobles y fixtures; definir aparte un entorno Oracle desechable antes de crear pruebas de integración que requieran una base real. |
| `PEND-006` | `OPEN` | Publicación/CI | Completar la barrera de publicación sobre el CI de calidad vigente: publicar sólo desde una versión etiquetada y autorizada, con procedencia y entorno protegidos. | `.github/workflows/quality.yaml`; workflow futuro de release; `pyproject.toml`; `CHANGELOG.md` | Definir el canal y las credenciales protegidas, validar primero en TestPyPI o un registro privado y diseñar la autorización de release; no publicar ni crear tags sin solicitud explícita. |
| `PEND-007` | `OPEN` | Documentación/contrato | Resolver que la configuración acepta `postgres` aunque sólo existe el adaptador Oracle. La instalación de desarrollo y las herramientas de calidad ya están alineadas. | `README.md`; `limitsdb/core/ldb_params_config.py`; `limitsdb/db/ldb_engine_loader.py` | Decidir si PostgreSQL se rechaza hasta implementarse o queda como capacidad futura, y después alinear código, ayuda y documentación con pruebas. |
| `PEND-008` | `OPEN` | Distribución | Definir los canales soportados para contenedor y eventual ejecutable autónomo sin asumir que un binario impide copia o reemplaza las obligaciones de licencia; el wheel ya cuenta con instalación limpia automatizada. | `pyproject.toml`; `README.md`; `.github/workflows/quality.yaml`; configuración futura de empaquetado y release | Evaluar contenedor, PyInstaller o Nuitka con el driver Oracle y documentar la matriz elegida antes de ofrecer binarios. |
